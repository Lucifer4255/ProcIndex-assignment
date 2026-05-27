"""PydanticAI adapter: registers sheets domain tools with the agent."""

from __future__ import annotations

import agent.core as _core
from pydantic_ai import RunContext

from agent.models import BookingSession
from tools.sheets import CallPriority, CallReason
import tools.sheets as _sheets


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
    return await _sheets.get_caller_history(ctx.deps, phone)


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

    Use for completed bookings, inquiries, or follow-ups (group parties,
    membership questions, waitlist, feedback). If a caller says a manager never
    called back about a birthday/group/private-session inquiry, use reason
    missed_callback, priority urgent, and callback_required true. Do NOT use for
    billing/refund/injury — use escalate_to_human instead.

    Args:
        reason: Why the caller reached out — booking, inquiry, group_booking,
            missed_callback, etc.
        summary: One or two sentence summary of the conversation or request.
        priority: normal for handled calls, high for new manager follow-up, urgent for missed callbacks.
        callback_required: True when a manager should call the caller back.
        notes: Optional extra detail (injury notes, party size, etc.).
        phone: Caller phone if not already in session.
        name: Caller name if not already in session.
    """
    return await _sheets.log_call(
        ctx.deps, reason, summary, priority, callback_required, notes, phone, name
    )
