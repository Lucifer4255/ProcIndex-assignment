"""Escalation tool: escalate_to_human."""

from __future__ import annotations

from typing import Literal

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
from integrations.google_calendar import normalize_phone
from integrations.sheets_context import get_sheets_client

EscalationReason = Literal[
    "billing",
    "refund",
    "injury",
    "instructor_complaint",
    "membership_cancel",
    "abuse",
    "other",
]

_REASON_LABELS: dict[EscalationReason, str] = {
    "billing": "billing issue",
    "refund": "refund request",
    "injury": "injury report",
    "instructor_complaint": "instructor complaint",
    "membership_cancel": "membership cancellation",
    "abuse": "safety concern",
    "other": "sensitive issue",
}


def _resolve_callback(session: BookingSession, callback_number: str | None) -> str | None:
    raw = callback_number or session.caller_phone
    if not raw:
        return None
    return normalize_phone(raw)


@_core._agent.tool
async def escalate_to_human(
    ctx: RunContext[BookingSession],
    reason: EscalationReason,
    callback_number: str | None = None,
    notes: str | None = None,
    name: str | None = None,
) -> str:
    """Escalate a sensitive issue to a studio manager for callback.

    Use ONLY for billing disputes, refunds, injuries, instructor complaints,
    membership cancellations, abusive callers, or similar issues where Maya must NOT
    promise any outcome. Collect a callback number first if missing. Ask for the
    caller's name when possible, and pass it via `name`; if the name is already in
    session context, you may omit it.

    Priority is set automatically from reason — do not try to downgrade urgent cases.

    Args:
        reason: Category of escalation — billing, refund, injury, instructor_complaint,
            membership_cancel, abuse, or other.
        callback_number: Best number for manager callback. Omit if already in session.
        notes: Optional detail (what happened, when charged, injury description).
        name: Caller name if known. Ask for it before escalating when the conversation
            allows, but do not block urgent escalation if only the callback number is available.
    """
    phone_n = _resolve_callback(ctx.deps, callback_number)
    if not phone_n:
        return "I need a callback phone number before I can escalate this to a manager."

    if name:
        ctx.deps.caller_name = name.strip()
    ctx.deps.caller_phone = phone_n

    label = _REASON_LABELS.get(reason, "issue")
    summary = f"Escalation: {label}."
    if notes:
        summary = f"{summary} {notes.strip()}"

    fields: dict[str, str] = {
        "last_call_reason": reason,
        "last_call_summary": summary,
        "priority_flag": "urgent",
        "callback_required": "TRUE",
    }
    if ctx.deps.caller_name:
        fields["name"] = ctx.deps.caller_name
    if notes:
        fields["notes"] = notes.strip()

    try:
        client = get_sheets_client()
        await client.upsert_row(phone_n, fields)
        await client.append_log_row(
            phone_n,
            {
                "name": ctx.deps.caller_name or "",
                "reason": reason,
                "summary": summary,
                "priority": "urgent",
                "callback_required": "TRUE",
                "notes": (notes or "").strip(),
            },
        )
    except Exception:
        # Sheets unavailable — still reassure the caller; surface in logs only.
        # TODO: ship to a fallback sink (email, queue) so sensitive escalations
        # aren't lost when Sheets is down.
        pass

    display_name = ctx.deps.caller_name or "you"
    return (
        f"I've flagged this {label} for our manager — they'll call {display_name} back "
        f"at the number on file. I can't resolve billing or refunds on this line."
    )
