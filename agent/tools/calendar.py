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

    return session.missing_for_booking()


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
    """Check class availability. Call with whatever info you have — date, time, class type all optional."""
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
                alt_text = ". ".join(format_slot_line(s) for s in available[:4])
                return f"No classes at that time. Available that day: {alt_text}."
            return "No classes available that day."
        lines = ". ".join(format_slot_line(s) for s in all_slots)
        return f"At that time: {lines}."

    # If we have all three, look up the specific slot.
    if ctx.deps.preferred_date and ctx.deps.preferred_time and ctx.deps.class_type:
        slot, error = await _resolve_target_slot(ctx.deps)
        if error:
            return error
        assert slot is not None
        if not slot.is_full:
            ctx.deps.slot_confirmed = True
            return f"{format_slot_line(slot)}. Want me to book you in?"
        # Full — show alternatives same class type same day
        day = slot.start.date()
        day_slots = await client.list_slots_for_day(day, slot.class_type)
        alternatives = [s for s in day_slots if not s.is_full and s.event_id != slot.event_id]
        if alternatives:
            alt_text = ". ".join(format_slot_line(s) for s in alternatives[:3])
            return f"{format_slot_line(slot)}. Other {slot.class_type} options that day: {alt_text}."
        return f"{format_slot_line(slot)}. No other open {slot.class_type} classes that day."

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
        alt_text = ". ".join(format_slot_line(s) for s in available[:4])
        return f"Open classes that day: {alt_text}."

    return "What date and time are you looking at?"


@_core._agent.tool
async def book_class(ctx: RunContext[BookingSession]) -> str:
    """Book the caller into the selected class slot using session details."""
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
) -> str:
    """Move an existing booking to a new class date and time."""
    if not ctx.deps.caller_phone:
        return "I need the caller phone number to find their existing booking."

    parsed = parse_time_string(new_time)
    if parsed is None:
        return "I need a valid new time like 6pm or 18:00."

    try:
        day = date.fromisoformat(new_date)
    except ValueError:
        return "I need a valid new date in YYYY-MM-DD format."

    hour, minute = parsed
    ctx.deps.preferred_date = new_date
    ctx.deps.preferred_time = f"{hour:02d}:{minute:02d}"

    if not ctx.deps.class_type:
        client = get_calendar_client()
        search_start = studio_datetime(date.today(), 0)
        current = await client.find_booking_by_phone(
            ctx.deps.caller_phone,
            search_start,
            studio_datetime(date.today() + timedelta(days=60), 23, 59),
        )
        if current:
            ctx.deps.class_type = current.class_type
        else:
            return "I couldn't find an existing booking for that phone number."

    client = get_calendar_client()
    target = await client.find_slot_at(ctx.deps.class_type, day, hour, minute)
    if target is None:
        return f"I don't see a {ctx.deps.class_type} class at that new date and time."
    if target.is_full:
        return f"That new slot is full. {format_slot_line(target)}."

    updated = await client.move_booking(
        ctx.deps.caller_phone,
        target,
        name=ctx.deps.caller_name,
    )
    ctx.deps.booking_confirmed = True
    ctx.deps.slot_confirmed = True
    return f"Rescheduled to {format_slot_line(updated)}."


@_core._agent.tool
async def cancel_booking(
    ctx: RunContext[BookingSession],
    date_value: str | None = None,
    time_value: str | None = None,
) -> str:
    """Cancel the caller's booking, optionally narrowed by date and time."""
    if not ctx.deps.caller_phone:
        return "I need the caller phone number to find their booking."

    if date_value or time_value:
        _merge_booking_fields(ctx.deps, date_value=date_value, time_value=time_value)

    client = get_calendar_client()

    if ctx.deps.preferred_date and ctx.deps.preferred_time and ctx.deps.class_type:
        slot, error = await _resolve_target_slot(ctx.deps)
        if error:
            return error
        assert slot is not None
        phone_n = normalize_phone(ctx.deps.caller_phone)
        if not any(normalize_phone(b.phone) == phone_n for b in slot.booked):
            return "I don't see a booking for that phone number in that class."
        await client.remove_booking(slot, phone_n)
        ctx.deps.booking_confirmed = False
        return f"Cancelled the booking for {format_slot_line(slot)}."

    search_start = studio_datetime(date.today(), 0)
    search_end = studio_datetime(date.today() + timedelta(days=60), 23, 59)
    current = await client.find_booking_by_phone(ctx.deps.caller_phone, search_start, search_end)
    if current is None:
        return "I couldn't find an existing booking for that phone number."

    await client.remove_booking(current, normalize_phone(ctx.deps.caller_phone))
    ctx.deps.booking_confirmed = False
    return f"Cancelled the booking for {format_slot_line(current)}."
