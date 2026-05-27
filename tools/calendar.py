"""Domain tools: check_slot, lookup_existing_bookings, book_class, reschedule, cancel_booking."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_STUDIO_TZ = ZoneInfo("America/Los_Angeles")

from agent.models import BookingSession
from integrations.calendar_context import get_calendar_client
from integrations.google_calendar import (
    format_slot_line,
    is_valid_phone,
    normalize_phone,
    parse_time_string,
    studio_datetime,
)
from integrations.sheets_context import get_sheets_client


def _is_upcoming(slot) -> bool:
    """Return True if the slot hasn't started yet (studio local time)."""
    now = datetime.now(tz=_STUDIO_TZ)
    return slot.start > now


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


async def _log_calendar_success(
    session: BookingSession,
    reason: str,
    summary: str,
    class_booked: str | None = None,
) -> None:
    if not session.caller_phone:
        return

    try:
        client = get_sheets_client()
        fields: dict[str, str] = {
            "last_call_reason": reason,
            "last_call_summary": summary,
        }
        if session.caller_name:
            fields["name"] = session.caller_name
        if class_booked:
            fields["last_class_booked"] = class_booked
        await client.upsert_row(session.caller_phone, fields)
        await client.append_log_row(
            session.caller_phone,
            {
                "name": session.caller_name or "",
                "reason": reason,
                "summary": summary,
                "priority": "normal",
                "callback_required": "FALSE",
            },
        )
    except Exception:
        return


# ---------------------------------------------------------------------------
# check_slot
# ---------------------------------------------------------------------------

async def check_slot(
    session: BookingSession,
    class_type: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
) -> str:
    _merge_booking_fields(session, class_type, date_value, time_value)

    client = get_calendar_client()

    if session.preferred_date and session.preferred_time and not session.class_type:
        try:
            day = date.fromisoformat(session.preferred_date)
        except ValueError:
            return "I need a valid date to check availability."
        parsed = parse_time_string(session.preferred_time)
        if parsed is None:
            return "I need a valid time to check availability."
        hour, minute = parsed
        target_dt = studio_datetime(day, hour, minute)
        all_slots = await client.list_class_slots(
            target_dt, target_dt + timedelta(minutes=1)
        )
        if not all_slots:
            day_slots = await client.list_slots_for_day(day)
            available = [s for s in day_slots if not s.is_full and _is_upcoming(s)]
            if available:
                alt_text = ". ".join(_annotate_slot(s, session) for s in available[:4])
                return f"No classes at that time. Available that day: {alt_text}."
            return "No classes available that day."
        lines = ". ".join(_annotate_slot(s, session) for s in all_slots)
        if all(s.is_full for s in all_slots):
            day_slots = await client.list_slots_for_day(day)
            available = [s for s in day_slots if not s.is_full and _is_upcoming(s)]
            if available:
                alt_text = ". ".join(_annotate_slot(s, session) for s in available[:4])
                return f"At that time: {lines}. Other options that day: {alt_text}."
            return f"At that time: {lines}. No other open classes that day."
        return f"At that time: {lines}."

    if session.preferred_date and session.preferred_time and session.class_type:
        slot, error = await _resolve_target_slot(session)
        if error:
            return error
        assert slot is not None
        if not slot.is_full:
            session.slot_confirmed = True
            return f"{_annotate_slot(slot, session)}. Want me to book you in?"
        day = slot.start.date()
        day_slots = await client.list_slots_for_day(day, slot.class_type)
        alternatives = [s for s in day_slots if not s.is_full and _is_upcoming(s) and s.event_id != slot.event_id]
        if alternatives:
            alt_text = ". ".join(_annotate_slot(s, session) for s in alternatives[:3])
            return f"{_annotate_slot(slot, session)}. Other {slot.class_type} options that day: {alt_text}."
        return f"{_annotate_slot(slot, session)}. No other open {slot.class_type} classes that day."

    if session.preferred_date:
        try:
            day = date.fromisoformat(session.preferred_date)
        except ValueError:
            return "I need a valid date to check availability."
        day_slots = await client.list_slots_for_day(day)
        available = [s for s in day_slots if not s.is_full and _is_upcoming(s)]
        if not available:
            return "No open classes remaining today." if day == date.today() else "No open classes that day."
        alt_text = ". ".join(_annotate_slot(s, session) for s in available[:4])
        return f"Open classes that day: {alt_text}."

    return "What date and time are you looking at?"


# ---------------------------------------------------------------------------
# lookup_existing_bookings
# ---------------------------------------------------------------------------

async def lookup_existing_bookings(
    session: BookingSession,
    caller_phone: str,
) -> str:
    _merge_booking_fields(session, caller_phone=caller_phone)
    client = get_calendar_client()
    today = date.today()
    start = studio_datetime(today, 0)
    end = studio_datetime(today + timedelta(days=60), 23, 59)
    slots = await client.list_bookings_by_phone(session.caller_phone, start, end)

    session.existing_bookings = [
        {
            "class_type": s.class_type,
            "date": s.start.date().isoformat(),
            "time": f"{s.start.hour:02d}:{s.start.minute:02d}",
            "event_id": s.event_id,
        }
        for s in slots
    ]

    if not session.caller_name:
        phone_target = normalize_phone(session.caller_phone)
        for s in slots:
            for b in s.booked:
                if normalize_phone(b.phone) == phone_target and b.name:
                    session.caller_name = b.name
                    break
            if session.caller_name:
                break

    if not slots:
        return "No upcoming bookings under that phone number."
    name_prefix = f"Booking under {session.caller_name}. " if session.caller_name else ""
    if len(slots) == 1:
        return f"{name_prefix}Found 1 booking on file: {format_slot_line(slots[0])}."
    summary = ", ".join(format_slot_line(s) for s in slots)
    return f"{name_prefix}Found {len(slots)} bookings on file: {summary}."


# ---------------------------------------------------------------------------
# book_class
# ---------------------------------------------------------------------------

async def book_class(
    session: BookingSession,
    class_type: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
    caller_name: str | None = None,
    caller_phone: str | None = None,
) -> str:
    _merge_booking_fields(
        session,
        class_type=class_type,
        date_value=date_value,
        time_value=time_value,
        caller_name=caller_name,
        caller_phone=caller_phone,
    )
    missing = session.missing_for_booking()
    if missing:
        return f"Still need: {', '.join(missing)} before I can complete the booking."

    if not is_valid_phone(session.caller_phone or ""):
        session.caller_phone = None
        return "That phone number doesn't look right — I need 10 digits. Could you read it back?"

    slot, error = await _resolve_target_slot(session)
    if error:
        return error
    assert slot is not None

    if slot.is_full:
        return f"That {slot.class_type} class is full. Ask me to check another time first."

    if not session.caller_name or not session.caller_phone:
        return "I still need the caller name and phone number."

    client = get_calendar_client()
    phone_n = normalize_phone(session.caller_phone)

    if any(normalize_phone(b.phone) == phone_n for b in slot.booked):
        return f"You're already booked for {format_slot_line(slot)}."

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
        updated = await client.add_booking(slot, session.caller_name, session.caller_phone)
    except ValueError:
        return f"That {slot.class_type} class just filled up. Want another time?"

    session.slot_confirmed = True
    session.booking_confirmed = True
    booked_summary = (
        f"{updated.class_type} {updated.start.strftime('%H:%M')} "
        f"{updated.start.date().isoformat()}"
    )
    await _log_calendar_success(
        session,
        "booking",
        f"Booked {session.caller_name} into {format_slot_line(updated)}.",
        class_booked=booked_summary,
    )
    return (
        f"Booked {session.caller_name} into {format_slot_line(updated)}. "
        f"Confirmation on file: {normalize_phone(session.caller_phone)}."
    )


# ---------------------------------------------------------------------------
# reschedule
# ---------------------------------------------------------------------------

async def reschedule(
    session: BookingSession,
    new_date: str,
    new_time: str,
    old_date: str | None = None,
    old_time: str | None = None,
    new_class_type: str | None = None,
    caller_phone: str | None = None,
    caller_name: str | None = None,
) -> str:
    _merge_booking_fields(session, caller_name=caller_name, caller_phone=caller_phone)
    if not session.caller_phone:
        return "I need the caller phone number to find their existing booking."

    resolved_old_date = old_date
    resolved_old_time = old_time
    if not resolved_old_date or not resolved_old_time:
        if session.existing_bookings and len(session.existing_bookings) == 1:
            only = session.existing_bookings[0]
            resolved_old_date = resolved_old_date or only.get("date")
            resolved_old_time = resolved_old_time or only.get("time")
        elif session.existing_bookings and len(session.existing_bookings) > 1:
            options = ", ".join(
                f"{b.get('class_type')} {b.get('time')} on {b.get('date')}"
                for b in session.existing_bookings
            )
            return f"You have multiple bookings — which one should I move? Options: {options}."
    if not resolved_old_date or not resolved_old_time:
        resolved_old_date = resolved_old_date or session.preferred_date
        resolved_old_time = resolved_old_time or session.preferred_time
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
    phone_n = normalize_phone(session.caller_phone)

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

    if target.event_id == existing.event_id:
        return f"You're already booked for {format_slot_line(existing)} — nothing to move."

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

    display_name = session.caller_name
    if not display_name:
        for b in existing.booked:
            if normalize_phone(b.phone) == phone_n:
                display_name = b.name
                break
    display_name = display_name or "Guest"

    await client.remove_booking(existing, phone_n)
    updated = await client.add_booking(target, display_name, phone_n)

    session.class_type = updated.class_type
    session.preferred_date = new_date
    session.preferred_time = f"{new_hour:02d}:{new_minute:02d}"
    session.booking_confirmed = True
    session.slot_confirmed = True
    new_summary = (
        f"{updated.class_type} {updated.start.strftime('%H:%M')} "
        f"{updated.start.date().isoformat()}"
    )
    await _log_calendar_success(
        session,
        "reschedule",
        f"Moved booking from {format_slot_line(existing)} to {format_slot_line(updated)}.",
        class_booked=new_summary,
    )
    return f"Moved booking from {format_slot_line(existing)} to {format_slot_line(updated)}."


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------

async def cancel_booking(
    session: BookingSession,
    date_value: str | None = None,
    time_value: str | None = None,
    caller_phone: str | None = None,
    caller_name: str | None = None,
) -> str:
    _merge_booking_fields(
        session,
        date_value=date_value,
        time_value=time_value,
        caller_name=caller_name,
        caller_phone=caller_phone,
    )
    if not session.caller_phone:
        return "I need the caller phone number to find their booking."

    client = get_calendar_client()
    phone_n = normalize_phone(session.caller_phone)

    if session.preferred_date and session.preferred_time and session.class_type:
        slot, error = await _resolve_target_slot(session)
        if error:
            return error
        assert slot is not None
        if not any(normalize_phone(b.phone) == phone_n for b in slot.booked):
            return "I don't see a booking for that phone number in that class."
        await client.remove_booking(slot, phone_n)
        session.booking_confirmed = False
        await _log_calendar_success(session, "cancel", f"Cancelled booking for {format_slot_line(slot)}.")
        return f"Cancelled the booking for {format_slot_line(slot)}."

    if session.existing_bookings and len(session.existing_bookings) == 1:
        only = session.existing_bookings[0]
        hh, mm = only["time"].split(":")
        target_dt = studio_datetime(date.fromisoformat(only["date"]), int(hh), int(mm))
        candidates = await client.list_class_slots(target_dt, target_dt + timedelta(minutes=1))
        match = next((s for s in candidates if s.event_id == only.get("event_id")), None)
        if match is not None:
            await client.remove_booking(match, phone_n)
            session.booking_confirmed = False
            await _log_calendar_success(session, "cancel", f"Cancelled booking for {format_slot_line(match)}.")
            return f"Cancelled the booking for {format_slot_line(match)}."

    if session.existing_bookings and len(session.existing_bookings) > 1:
        options = ", ".join(
            f"{b.get('class_type')} {b.get('time')} on {b.get('date')}"
            for b in session.existing_bookings
        )
        return f"You have multiple bookings — which one should I cancel? Options: {options}."

    search_start = studio_datetime(date.today(), 0)
    search_end = studio_datetime(date.today() + timedelta(days=60), 23, 59)
    current = await client.find_booking_by_phone(session.caller_phone, search_start, search_end)
    if current is None:
        return "I couldn't find an existing booking for that phone number."

    await client.remove_booking(current, phone_n)
    session.booking_confirmed = False
    await _log_calendar_success(session, "cancel", f"Cancelled booking for {format_slot_line(current)}.")
    return f"Cancelled the booking for {format_slot_line(current)}."
