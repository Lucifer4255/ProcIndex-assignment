"""PydanticAI adapter: registers calendar domain tools with the agent."""

from __future__ import annotations

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
import tools.calendar as _cal


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
    return await _cal.check_slot(ctx.deps, class_type, date_value, time_value)


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
    return await _cal.lookup_existing_bookings(ctx.deps, caller_phone)


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
    return await _cal.book_class(ctx.deps, class_type, date_value, time_value, caller_name, caller_phone)


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
    return await _cal.reschedule(
        ctx.deps, new_date, new_time, old_date, old_time, new_class_type, caller_phone, caller_name
    )


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
    return await _cal.cancel_booking(ctx.deps, date_value, time_value, caller_phone, caller_name)
