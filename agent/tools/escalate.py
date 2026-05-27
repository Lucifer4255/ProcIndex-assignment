"""PydanticAI adapter: registers escalation domain tool with the agent."""

from __future__ import annotations

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
from tools.escalate import EscalationReason
import tools.escalate as _escalate


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
    membership cancellations, abusive callers, or similar issues where Elliot must NOT
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
    return await _escalate.escalate_to_human(ctx.deps, reason, callback_number, notes, name)
