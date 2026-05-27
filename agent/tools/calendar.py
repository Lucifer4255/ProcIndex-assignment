"""Calendar tools: check_slot, book_class, reschedule, cancel."""

from __future__ import annotations

from datetime import date, timedelta

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
from integrations.calendar_context import get_calendar_client
from integrations.google_calendar import (
    format_slot_line,
    normalize_phone,
    parse_time_string,
    studio_datetime,
)


def _merge_booking_fields(
    session: BookingSession,
    class_type: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
    caller_name: str | None = None,
    caller_phone: str | None = None,
) -> list[str]:
    if class_type:
        session.class_type = class_type.strip().title()
    if date_value:
        session.preferred_date = date_value.strip()
    if time_value:
        parsed = parse_time_string(time_value)
        if parsed is None:
            return ["time (use a valid time like 6pm or 18:00)"]
        hour, minute = parsed
        session.preferred_time = f"{hour:02d}:{minute:02d}"
    if caller_name:
        session.caller_name = caller_name.strip()
    if caller_phone:
        session.caller_phone = caller_phone.strip()

    return session.missing_for_booking()


def _annotate_slot(slot, session: BookingSession) -> str:
    """Format a slot line; mark it as the caller's existing booking when matched."""
    line = format_slot_line(slot)
    if session.existing_bookings and any(
        b.get("event_id") == slot.event_id for b in session.existing_bookings
    ):
        return f"{line} (your current booking)"
    return line


async def _resolve_target_slot(session: BookingSession):
    missing = session.missing_for_booking()
    if "class type" in missing or "date" in missing or "time" in missing:
        return None, "Still need: " + ", ".join(missing)

    try:
        day = date.fromisoformat(session.preferred_date or "")
    except ValueError:
        return None, "I need a valid date in YYYY-MM-DD format."

    parsed = parse_time_string(session.preferred_time or "")
    if parsed is None:
        return None, "I need a valid time like 6pm or 18:00."

    hour, minute = parsed
    client = get_calendar_client()
    slot = await client.find_slot_at(session.class_type or "", day, hour, minute)
    if slot is None:
        return (
            None,
            f"I don't see a {session.class_type} class at that date and time on the schedule.",
        )
    return slot, None


@_core._agent.tool
async def check_slot(
    ctx: RunContext[BookingSession],
    class_type: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
) -> str:
    """Check class slot availability at Solstice Pilates.

    Call this IMMEDIATELY whenever the caller mentions availability, schedules, open spots,
    or wants to book a class — even if you only have partial information. Do NOT ask for
    missing details before calling; pass what you have and the tool returns what is available.
    You can ask follow-up questions after seeing the results.

    Args:
        class_type: Class name — 'Reformer', 'Mat', or 'Tower'. Omit if the caller has not specified.
        date_value: Date in YYYY-MM-DD format, e.g. '2026-05-29'. Omit if not yet known.
        time_value: Time in any natural format — '6pm', '18:00', '9am', '9:30am'. Omit if not yet known.
    """
    _merge_booking_fields(ctx.deps, class_type, date_value, time_value)

    client = get_calendar_client()

    # If we have date + time but no class type, show all classes at that time.
    if ctx.deps.preferred_date and ctx.deps.preferred_time and not ctx.deps.class_type:
        try:
            day = date.fromisoformat(ctx.deps.preferred_date)
        except ValueError:
            return "I need a valid date to check availability."
        parsed = parse_time_string(ctx.deps.preferred_time)
        if parsed is None:
            return "I need a valid time to check availability."
        hour, minute = parsed
        target_dt = studio_datetime(day, hour, minute)
        all_slots = await client.list_class_slots(
            target_dt, target_dt + timedelta(minutes=1)
        )
        if not all_slots:
            # Nothing at that exact time — show what's available that day
            day_slots = await client.list_slots_for_day(day)
            available = [s for s in day_slots if not s.is_full]
            if available:
                alt_text = ". ".join(_annotate_slot(s, ctx.deps) for s in available[:4])
                return f"No classes at that time. Available that day: {alt_text}."
            return "No classes available that day."
        lines = ". ".join(_annotate_slot(s, ctx.deps) for s in all_slots)
        return f"At that time: {lines}."

    # If we have all three, look up the specific slot.
    if ctx.deps.preferred_date and ctx.deps.preferred_time and ctx.deps.class_type:
        slot, error = await _resolve_target_slot(ctx.deps)
        if error:
            return error
        assert slot is not None
        if not slot.is_full:
            ctx.deps.slot_confirmed = True
            return f"{_annotate_slot(slot, ctx.deps)}. Want me to book you in?"
        # Full — show alternatives same class type same day
        day = slot.start.date()
        day_slots = await client.list_slots_for_day(day, slot.class_type)
        alternatives = [s for s in day_slots if not s.is_full and s.event_id != slot.event_id]
        if alternatives:
            alt_text = ". ".join(_annotate_slot(s, ctx.deps) for s in alternatives[:3])
            return f"{_annotate_slot(slot, ctx.deps)}. Other {slot.class_type} options that day: {alt_text}."
        return f"{_annotate_slot(slot, ctx.deps)}. No other open {slot.class_type} classes that day."

    # If we only have a date, show all available slots that day.
    if ctx.deps.preferred_date:
        try:
            day = date.fromisoformat(ctx.deps.preferred_date)
        except ValueError:
            return "I need a valid date to check availability."
        day_slots = await client.list_slots_for_day(day)
        available = [s for s in day_slots if not s.is_full]
        if not available:
            return "No open classes that day."
        alt_text = ". ".join(_annotate_slot(s, ctx.deps) for s in available[:4])
        return f"Open classes that day: {alt_text}."

    return "What date and time are you looking at?"


@_core._agent.tool
async def lookup_existing_bookings(
    ctx: RunContext[BookingSession],
    caller_phone: str,
) -> str:
    """Fetch all of a caller's current bookings in the next 60 days.

    Call this as the FIRST step whenever the caller wants to reschedule, cancel, or
    asks about "my booking" — immediately after getting their phone number, before
    asking what they want to change. The result is stored in session context so
    subsequent tools (check_slot, reschedule, cancel_booking) know which slots
    belong to this caller.

    Args:
        caller_phone: The caller's phone number — REQUIRED.
    """
    _merge_booking_fields(ctx.deps, caller_phone=caller_phone)
    client = get_calendar_client()
    today = date.today()
    start = studio_datetime(today, 0)
    end = studio_datetime(today + timedelta(days=60), 23, 59)
    slots = await client.list_bookings_by_phone(ctx.deps.caller_phone, start, end)

    ctx.deps.existing_bookings = [
        {
            "class_type": s.class_type,
            "date": s.start.date().isoformat(),
            "time": f"{s.start.hour:02d}:{s.start.minute:02d}",
            "event_id": s.event_id,
        }
        for s in slots
    ]

    if not slots:
        return "No upcoming bookings under that phone number."
    if len(slots) == 1:
        return f"Found 1 booking on file: {format_slot_line(slots[0])}."
    summary = ", ".join(format_slot_line(s) for s in slots)
    return f"Found {len(slots)} bookings on file: {summary}."


@_core._agent.tool
async def book_class(
    ctx: RunContext[BookingSession],
    class_type: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
    caller_name: str | None = None,
    caller_phone: str | None = None,
) -> str:
    """Confirm and complete a class booking for the caller.

    Call this only after: (1) the caller has confirmed they want a specific slot,
    AND (2) you have collected their full name and phone number.
    PASS every piece of information you have collected as arguments — the tool needs
    name and phone explicitly because they are not stored anywhere else. Previously
    captured fields (class_type, date, time) can be omitted if check_slot already
    saved them, but pass them again if unsure.

    Args:
        class_type: Class name — 'Reformer', 'Mat', or 'Tower'. Pass if not yet stored.
        date_value: Date in YYYY-MM-DD format, e.g. '2026-05-28'. Pass if not yet stored.
        time_value: Time in any natural format — '9am', '6pm', '18:00'. Pass if not yet stored.
        caller_name: The caller's name as they gave it, e.g. 'Sara'. REQUIRED — always pass.
        caller_phone: The caller's phone number as they gave it, e.g. '+14155550190'. REQUIRED — always pass.
    """
    _merge_booking_fields(
        ctx.deps,
        class_type=class_type,
        date_value=date_value,
        time_value=time_value,
        caller_name=caller_name,
        caller_phone=caller_phone,
    )
    missing = ctx.deps.missing_for_booking()
    if missing:
        return f"Still need: {', '.join(missing)} before I can complete the booking."

    slot, error = await _resolve_target_slot(ctx.deps)
    if error:
        return error
    assert slot is not None

    if slot.is_full:
        return (
            f"That {slot.class_type} class is full. "
            "Ask me to check another time first."
        )

    if not ctx.deps.caller_name or not ctx.deps.caller_phone:
        return "I still need the caller name and phone number."

    client = get_calendar_client()
    phone_n = normalize_phone(ctx.deps.caller_phone)

    # Same slot duplicate
    if any(normalize_phone(b.phone) == phone_n for b in slot.booked):
        return f"You're already booked for {format_slot_line(slot)}."

    # Same-time double-booking across classes
    siblings = await client.list_class_slots(slot.start, slot.start + timedelta(minutes=1))
    conflict = next(
        (
            s for s in siblings
            if s.event_id != slot.event_id
            and any(normalize_phone(b.phone) == phone_n for b in s.booked)
        ),
        None,
    )
    if conflict is not None:
        return f"You already have a booking at that time: {format_slot_line(conflict)}."

    try:
        updated = await client.add_booking(
            slot,
            ctx.deps.caller_name,
            ctx.deps.caller_phone,
        )
    except ValueError:
        return f"That {slot.class_type} class just filled up. Want another time?"

    ctx.deps.slot_confirmed = True
    ctx.deps.booking_confirmed = True
    return (
        f"Booked {ctx.deps.caller_name} into {format_slot_line(updated)}. "
        f"Confirmation on file: {normalize_phone(ctx.deps.caller_phone)}."
    )


@_core._agent.tool
async def reschedule(
    ctx: RunContext[BookingSession],
    new_date: str,
    new_time: str,
    old_date: str | None = None,
    old_time: str | None = None,
    new_class_type: str | None = None,
    caller_phone: str | None = None,
    caller_name: str | None = None,
) -> str:
    """Move the caller's existing booking from one date/time to a different date/time.

    Call this when the caller wants to reschedule, move, or change an existing booking.
    You MUST have the new_date and new_time the caller wants to move to — ask for them
    if not given, do not guess. The existing booking's date/time can be passed as
    old_date / old_time when the caller specifies them; otherwise the tool falls back
    to the most recently discussed slot in session context. The new slot can be a
    DIFFERENT class type than the old one — pass `new_class_type` when the caller
    explicitly chooses one (e.g. "move my Mat 9am to the Reformer 7pm"). If you omit
    it, the tool first looks for the same class type as the old booking, then falls
    back to any single class available at the new time. Caller phone is required.
    Do NOT call `check_slot` for a reschedule request — call this tool directly.

    Args:
        new_date: Date the caller wants to move TO in YYYY-MM-DD, e.g. '2026-05-30'. REQUIRED.
        new_time: Time the caller wants to move TO — '9am', '6pm', '18:00'. REQUIRED.
        old_date: Date of the EXISTING booking in YYYY-MM-DD. Omit to use session context.
        old_time: Time of the EXISTING booking — '9am', '6pm'. Omit to use session context.
        new_class_type: Class type to move TO — 'Reformer', 'Mat', 'Tower'. Pass when the
            caller is switching class types; omit when keeping the same class.
        caller_phone: The caller's phone number — REQUIRED to locate their existing booking.
        caller_name: The caller's name, optional. Used as the display name on the new slot.
    """
    _merge_booking_fields(ctx.deps, caller_name=caller_name, caller_phone=caller_phone)
    if not ctx.deps.caller_phone:
        return "I need the caller phone number to find their existing booking."

    resolved_old_date = old_date
    resolved_old_time = old_time
    if not resolved_old_date or not resolved_old_time:
        if ctx.deps.existing_bookings and len(ctx.deps.existing_bookings) == 1:
            only = ctx.deps.existing_bookings[0]
            resolved_old_date = resolved_old_date or only.get("date")
            resolved_old_time = resolved_old_time or only.get("time")
        elif ctx.deps.existing_bookings and len(ctx.deps.existing_bookings) > 1:
            options = ", ".join(
                f"{b.get('class_type')} {b.get('time')} on {b.get('date')}"
                for b in ctx.deps.existing_bookings
            )
            return f"You have multiple bookings — which one should I move? Options: {options}."
    if not resolved_old_date or not resolved_old_time:
        resolved_old_date = resolved_old_date or ctx.deps.preferred_date
        resolved_old_time = resolved_old_time or ctx.deps.preferred_time
    if not resolved_old_date or not resolved_old_time:
        return "I need the date and time of your existing booking to move it."

    try:
        old_day = date.fromisoformat(resolved_old_date)
    except ValueError:
        return "I need a valid existing-booking date in YYYY-MM-DD format."
    old_parsed = parse_time_string(resolved_old_time)
    if old_parsed is None:
        return "I need a valid existing-booking time like 9am or 18:00."
    old_hour, old_minute = old_parsed

    try:
        new_day = date.fromisoformat(new_date)
    except ValueError:
        return "I need a valid new date in YYYY-MM-DD format."
    new_parsed = parse_time_string(new_time)
    if new_parsed is None:
        return "I need a valid new time like 9am or 18:00."
    new_hour, new_minute = new_parsed

    client = get_calendar_client()
    phone_n = normalize_phone(ctx.deps.caller_phone)

    # Find the existing slot at old_date/old_time that has this phone booked
    old_dt = studio_datetime(old_day, old_hour, old_minute)
    candidates = await client.list_class_slots(old_dt, old_dt + timedelta(minutes=1))
    existing = next(
        (s for s in candidates if any(normalize_phone(b.phone) == phone_n for b in s.booked)),
        None,
    )
    if existing is None:
        return f"I don't see a booking under that phone at {old_date} {old_time}."

    desired_class = (new_class_type or existing.class_type).strip().title()
    target = await client.find_slot_at(desired_class, new_day, new_hour, new_minute)
    if target is None and not new_class_type:
        # Caller didn't pick a specific class — try whatever's at that time
        new_dt = studio_datetime(new_day, new_hour, new_minute)
        all_at_time = await client.list_class_slots(new_dt, new_dt + timedelta(minutes=1))
        open_at_time = [s for s in all_at_time if not s.is_full]
        if len(open_at_time) == 1:
            target = open_at_time[0]
        elif len(open_at_time) > 1:
            options = ", ".join(format_slot_line(s) for s in open_at_time)
            return f"No {existing.class_type} at {new_time} on {new_date}, but: {options}. Which one?"
    if target is None:
        return f"I don't see a {desired_class} class at {new_date} {new_time}."
    if target.is_full:
        return f"That new slot is full. {format_slot_line(target)}."

    # No-op guard: trying to "move" to the same slot they're already in
    if target.event_id == existing.event_id:
        return f"You're already booked for {format_slot_line(existing)} — nothing to move."

    # Same-time conflict across classes — e.g. already booked into another class at new_time
    new_dt_check = studio_datetime(new_day, new_hour, new_minute)
    siblings = await client.list_class_slots(new_dt_check, new_dt_check + timedelta(minutes=1))
    conflict = next(
        (
            s for s in siblings
            if s.event_id not in (target.event_id, existing.event_id)
            and any(normalize_phone(b.phone) == phone_n for b in s.booked)
        ),
        None,
    )
    if conflict is not None:
        return f"You already have another booking at that time: {format_slot_line(conflict)}."

    display_name = ctx.deps.caller_name
    if not display_name:
        for b in existing.booked:
            if normalize_phone(b.phone) == phone_n:
                display_name = b.name
                break
    display_name = display_name or "Guest"

    await client.remove_booking(existing, phone_n)
    updated = await client.add_booking(target, display_name, phone_n)

    ctx.deps.class_type = updated.class_type
    ctx.deps.preferred_date = new_date
    ctx.deps.preferred_time = f"{new_hour:02d}:{new_minute:02d}"
    ctx.deps.booking_confirmed = True
    ctx.deps.slot_confirmed = True
    return f"Moved booking from {format_slot_line(existing)} to {format_slot_line(updated)}."


@_core._agent.tool
async def cancel_booking(
    ctx: RunContext[BookingSession],
    date_value: str | None = None,
    time_value: str | None = None,
    caller_phone: str | None = None,
    caller_name: str | None = None,
) -> str:
    """Cancel the caller's existing class booking.

    Call this when the caller wants to cancel or remove a booking.
    Do NOT call `check_slot` for a cancellation — `cancel_booking` finds the slot itself.
    The caller's phone number is required to locate their booking; if you do not already
    have it, ask the caller first and pass it as `caller_phone`.
    If date and time are provided, cancels that specific slot. Otherwise finds their
    next upcoming booking automatically.

    Args:
        date_value: Date of the booking to cancel in YYYY-MM-DD format. Omit to auto-find.
        time_value: Time of the booking to cancel, e.g. '6pm'. Omit to auto-find.
        caller_phone: The caller's phone number — REQUIRED. Pass it explicitly the first
            time you call this tool so the lookup can find their existing booking.
        caller_name: The caller's name, optional.
    """
    _merge_booking_fields(
        ctx.deps,
        date_value=date_value,
        time_value=time_value,
        caller_name=caller_name,
        caller_phone=caller_phone,
    )
    if not ctx.deps.caller_phone:
        return "I need the caller phone number to find their booking."

    client = get_calendar_client()
    phone_n = normalize_phone(ctx.deps.caller_phone)

    # Specific slot if class_type + date + time are all set
    if ctx.deps.preferred_date and ctx.deps.preferred_time and ctx.deps.class_type:
        slot, error = await _resolve_target_slot(ctx.deps)
        if error:
            return error
        assert slot is not None
        if not any(normalize_phone(b.phone) == phone_n for b in slot.booked):
            return "I don't see a booking for that phone number in that class."
        await client.remove_booking(slot, phone_n)
        ctx.deps.booking_confirmed = False
        return f"Cancelled the booking for {format_slot_line(slot)}."

    # Use existing_bookings context if available
    if ctx.deps.existing_bookings and len(ctx.deps.existing_bookings) == 1:
        only = ctx.deps.existing_bookings[0]
        hh, mm = only["time"].split(":")
        target_dt = studio_datetime(date.fromisoformat(only["date"]), int(hh), int(mm))
        candidates = await client.list_class_slots(target_dt, target_dt + timedelta(minutes=1))
        match = next((s for s in candidates if s.event_id == only.get("event_id")), None)
        if match is not None:
            await client.remove_booking(match, phone_n)
            ctx.deps.booking_confirmed = False
            return f"Cancelled the booking for {format_slot_line(match)}."

    if ctx.deps.existing_bookings and len(ctx.deps.existing_bookings) > 1:
        options = ", ".join(
            f"{b.get('class_type')} {b.get('time')} on {b.get('date')}"
            for b in ctx.deps.existing_bookings
        )
        return f"You have multiple bookings — which one should I cancel? Options: {options}."

    # Final fallback — scan calendar for first booking
    search_start = studio_datetime(date.today(), 0)
    search_end = studio_datetime(date.today() + timedelta(days=60), 23, 59)
    current = await client.find_booking_by_phone(ctx.deps.caller_phone, search_start, search_end)
    if current is None:
        return "I couldn't find an existing booking for that phone number."

    await client.remove_booking(current, phone_n)
    ctx.deps.booking_confirmed = False
    return f"Cancelled the booking for {format_slot_line(current)}."
