"""Sheets tools: get_caller_history, log_call."""

from __future__ import annotations

from typing import Literal

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
from integrations.google_calendar import normalize_phone
from integrations.sheets_context import get_sheets_client

CallReason = Literal[
    "booking",
    "reschedule",
    "cancel",
    "inquiry",
    "follow_up",
    "group_booking",
    "membership_inquiry",
    "waitlist",
    "feedback",
    "lost_and_found",
    "late_arrival",
    "complaint",
]

CallPriority = Literal["normal", "high", "urgent"]


def _resolve_phone(session: BookingSession, phone: str | None) -> str | None:
    raw = phone or session.caller_phone
    if not raw:
        return None
    return normalize_phone(raw)


def _format_class_booked(session: BookingSession) -> str:
    # Only treat preferred_* as a real booking when booking_confirmed is set.
    # Otherwise these fields just reflect the last slot browsed via check_slot
    # and would record a class the caller never booked.
    if not session.booking_confirmed:
        return ""
    if not session.class_type or not session.preferred_time:
        return ""
    date_part = session.preferred_date or ""
    return f"{session.class_type} {session.preferred_time} {date_part}".strip()


@_core._agent.tool
async def get_caller_history(
    ctx: RunContext[BookingSession],
    phone: str | None = None,
) -> str:
    """Look up a returning caller in the Contacts sheet by phone number.

    Call this early when you have the caller's phone and want to greet them by name
    or reference their last visit. Stores the result on session context for later turns.

    Args:
        phone: Caller phone in any format. Omit to use phone already in session.
    """
    phone_n = _resolve_phone(ctx.deps, phone)
    if not phone_n:
        return "I need a phone number to look up caller history."

    ctx.deps.caller_phone = phone_n
    try:
        client = get_sheets_client()
        row = await client.get_row_by_phone(phone_n)
    except Exception:
        ctx.deps.caller_history = None
        return "Caller history unavailable right now — treat as a first-time caller."
    if row is None:
        ctx.deps.caller_history = None
        return "No prior record for that phone number — treat as a first-time caller."

    history = {
        "name": row.get("name") or "",
        "last_called": row.get("last_called") or "",
        "call_count": row.get("call_count") or 0,
        "last_class_booked": row.get("last_class_booked") or "",
        "last_call_reason": row.get("last_call_reason") or "",
        "last_call_summary": row.get("last_call_summary") or "",
        "priority_flag": row.get("priority_flag") or "normal",
    }
    ctx.deps.caller_history = history

    if history["name"] and not ctx.deps.caller_name:
        ctx.deps.caller_name = history["name"]

    name = history["name"] or "there"
    last_class = history["last_class_booked"]
    if last_class:
        return f"Returning caller: {name}. Last class booked: {last_class}."
    return f"Returning caller: {name}. No prior class on file."


@_core._agent.tool
async def log_call(
    ctx: RunContext[BookingSession],
    reason: CallReason,
    summary: str,
    priority: CallPriority = "normal",
    callback_required: bool = False,
    notes: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> str:
    """Log this call or a follow-up request to the Contacts sheet.

    Use for completed bookings, inquiries, or non-urgent follow-ups (group parties,
    membership questions, waitlist, feedback). Do NOT use for billing/refund/injury —
    use escalate_to_human instead.

    Args:
        reason: Why the caller reached out — booking, inquiry, group_booking, etc.
        summary: One or two sentence summary of the conversation or request.
        priority: normal for handled calls, high for manager follow-up that is not urgent.
        callback_required: True when a manager should call the caller back.
        notes: Optional extra detail (injury notes, party size, etc.).
        phone: Caller phone if not already in session.
        name: Caller name if not already in session.
    """
    phone_n = _resolve_phone(ctx.deps, phone)
    if not phone_n:
        return "I need a phone number before I can log this call."

    if name:
        ctx.deps.caller_name = name.strip()
    ctx.deps.caller_phone = phone_n

    fields: dict[str, str] = {
        "last_call_reason": reason,
        "last_call_summary": summary.strip(),
        "priority_flag": priority,
        "callback_required": "TRUE" if callback_required else "FALSE",
    }
    if ctx.deps.caller_name:
        fields["name"] = ctx.deps.caller_name

    booked = _format_class_booked(ctx.deps)
    if booked and reason in ("booking", "reschedule"):
        fields["last_class_booked"] = booked

    if notes:
        fields["notes"] = notes.strip()

    try:
        client = get_sheets_client()
        await client.upsert_row(phone_n, fields)
    except Exception:
        # Don't crash the conversation if the sheet is down — log path is best-effort.
        if callback_required:
            return "I've noted that — a manager will follow up at the number on file."
        return "Got it."

    if callback_required:
        return "Logged for manager follow-up. They will reach out at the number on file."
    return "Call logged."
