"""POST /vapi/webhook — Phase 2 Vapi server-message handler."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent.models import BookingSession
from agent.storage import SessionStore
from agent.tools.calendar import (
    book_class_impl,
    cancel_booking_impl,
    check_slot_impl,
    lookup_existing_bookings_impl,
    reschedule_impl,
)
from agent.tools.escalate import escalate_to_human_impl
from agent.tools.sheets import get_caller_history_impl, log_call_impl

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi")


async def get_current_date_impl(session: Any) -> str:
    today = date.today()
    return (
        f"Today is {today.strftime('%A, %B %d, %Y')} ({today.isoformat()}). "
        f"Studio timezone is America/Los_Angeles. "
        f"Use this to resolve relative phrases like 'this Saturday', 'next Monday', 'this week'."
    )

# Maps Vapi tool names → _impl callables.
# Signature: impl(session, **kwargs) -> str
_TOOL_DISPATCH: dict[str, Any] = {
    "get_current_date": get_current_date_impl,
    "check_slot": check_slot_impl,
    "lookup_existing_bookings": lookup_existing_bookings_impl,
    "book_class": book_class_impl,
    "reschedule": reschedule_impl,
    "cancel_booking": cancel_booking_impl,
    "get_caller_history": get_caller_history_impl,
    "log_call": log_call_impl,
    "escalate_to_human": escalate_to_human_impl,
}


def _get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


async def _load_session(store: SessionStore, call_id: str) -> BookingSession:
    loaded = await store.get(call_id)
    if loaded is None:
        return BookingSession(call_id=call_id)
    session, _ = loaded
    return session


async def _save_session(store: SessionStore, session: BookingSession) -> None:
    # Phase 2 has no PydanticAI message history — save empty list so the
    # same Redis key format is used and Phase 1 sessions are not polluted.
    await store.save(session.call_id, session, [])


async def _handle_tool_calls(payload: dict[str, Any], store: SessionStore) -> JSONResponse:
    """Execute each tool call and return results in Vapi's expected format."""
    call_id: str = (payload.get("call") or {}).get("id", "unknown")
    session = await _load_session(store, call_id)

    tool_list = payload.get("toolWithToolCallList") or payload.get("toolCalls") or []
    results: list[dict[str, str]] = []

    for item in tool_list:
        # item shape when parsed as raw dict:
        # { "toolCall": { "id": "...", "function": { "name": "...", "arguments": "{...}" } } }
        tool_call = item.get("toolCall") or item  # fallback for flat shape
        tc_id: str = tool_call.get("id", "")
        fn = tool_call.get("function") or {}
        name: str = fn.get("name", "")
        raw_args: str = fn.get("arguments", "{}")

        if isinstance(raw_args, dict):
            kwargs = raw_args
        else:
            try:
                kwargs = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                kwargs = {}

        impl = _TOOL_DISPATCH.get(name)
        if impl is None:
            result = f"Unknown tool: {name}"
        else:
            try:
                result = await impl(session, **kwargs)
            except Exception as exc:
                logger.exception("Tool %s raised", name)
                result = f"Tool error: {exc}"

        results.append({"toolCallId": tc_id, "result": str(result)})

    await _save_session(store, session)
    return JSONResponse({"results": results})


async def _handle_end_of_call(payload: dict[str, Any], store: SessionStore) -> JSONResponse:
    call_id: str = (payload.get("call") or {}).get("id", "")
    if call_id:
        await store.delete(call_id)
    return JSONResponse({})


@router.post("/webhook")
async def vapi_webhook(request: Request) -> JSONResponse:
    """Receive Vapi server messages and respond with tool results."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({}, status_code=200)

    # Vapi wraps everything under a top-level "message" key.
    msg: dict[str, Any] = body.get("message") or body
    msg_type: str = msg.get("type", "")

    store = _get_store(request)

    if msg_type == "tool-calls":
        return await _handle_tool_calls(msg, store)

    if msg_type == "end-of-call-report":
        return await _handle_end_of_call(msg, store)

    # All other message types (status-update, conversation-update, etc.) → ack.
    return JSONResponse({})
