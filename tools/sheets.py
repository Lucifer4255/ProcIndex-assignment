"""Domain tools: get_caller_history, log_call."""

from __future__ import annotations

from typing import Literal

from agent.models import BookingSession
from integrations.google_calendar import normalize_phone
from integrations.sheets_context import get_sheets_client

CallReason = Literal[
    "booking",
    "reschedule",
    "cancel",
    "inquiry",
    "follow_up",
    "missed_callback",
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
    if not session.booking_confirmed:
        return ""
    if not session.class_type or not session.preferred_time:
        return ""
    date_part = session.preferred_date or ""
    return f"{session.class_type} {session.preferred_time} {date_part}".strip()


# ---------------------------------------------------------------------------
# get_caller_history
# ---------------------------------------------------------------------------

async def get_caller_history(
    session: BookingSession,
    phone: str | None = None,
) -> str:
    phone_n = _resolve_phone(session, phone)
    if not phone_n:
        return "I need a phone number to look up caller history."

    session.caller_phone = phone_n
    try:
        client = get_sheets_client()
        row = await client.get_row_by_phone(phone_n)
    except Exception:
        session.caller_history = None
        return "Caller history unavailable right now — treat as a first-time caller."
    if row is None:
        session.caller_history = None
        return "No prior record for that phone number — treat as a first-time caller."

    history = {
        "name": row.get("name") or "",
        "last_called": row.get("last_called") or "",
        "call_count": row.get("call_count") or 0,
        "last_class_booked": row.get("last_class_booked") or "",
        "last_call_reason": row.get("last_call_reason") or "",
        "last_call_summary": row.get("last_call_summary") or "",
        "priority_flag": row.get("priority_flag") or "normal",
        "callback_required": row.get("callback_required") or "FALSE",
        "notes": row.get("notes") or "",
    }
    session.caller_history = history

    if history["name"] and not session.caller_name:
        session.caller_name = history["name"]

    name = history["name"] or "there"
    last_class = history["last_class_booked"]
    if last_class:
        return f"Returning caller: {name}. Last class booked: {last_class}."
    return f"Returning caller: {name}. No prior class on file."


# ---------------------------------------------------------------------------
# log_call
# ---------------------------------------------------------------------------

async def log_call(
    session: BookingSession,
    reason: CallReason,
    summary: str,
    priority: CallPriority = "normal",
    callback_required: bool = False,
    notes: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> str:
    phone_n = _resolve_phone(session, phone)
    if not phone_n:
        return "I need a phone number before I can log this call."

    if name:
        session.caller_name = name.strip()
    session.caller_phone = phone_n

    if reason == "missed_callback":
        priority = "urgent"
        callback_required = True

    fields: dict[str, str] = {
        "last_call_reason": reason,
        "last_call_summary": summary.strip(),
        "priority_flag": priority,
        "callback_required": "TRUE" if callback_required else "FALSE",
    }
    if session.caller_name:
        fields["name"] = session.caller_name

    booked = _format_class_booked(session)
    if booked and reason in ("booking", "reschedule"):
        fields["last_class_booked"] = booked

    if notes:
        fields["notes"] = notes.strip()

    try:
        client = get_sheets_client()
        await client.upsert_row(phone_n, fields)
        await client.append_log_row(
            phone_n,
            {
                "name": session.caller_name or "",
                "reason": reason,
                "summary": summary.strip(),
                "priority": priority,
                "callback_required": "TRUE" if callback_required else "FALSE",
                "notes": (notes or "").strip(),
            },
        )
    except Exception:
        if callback_required:
            return "I've noted that — a manager will follow up at the number on file."
        return "Got it."

    if callback_required:
        return "Logged for manager follow-up. They will reach out at the number on file."
    return "Call logged."
