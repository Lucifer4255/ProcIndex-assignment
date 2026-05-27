"""VapiService — orchestrates session management and tool dispatch for Vapi calls."""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.models import BookingSession
from agent.storage import SessionStore
import tools.calendar as _cal
import tools.clock as _clock
import tools.escalate as _escalate
import tools.sheets as _sheets

logger = logging.getLogger(__name__)

_TOOL_DISPATCH: dict[str, Any] = {
    "get_current_date": _clock.get_current_date,
    "check_slot": _cal.check_slot,
    "lookup_existing_bookings": _cal.lookup_existing_bookings,
    "book_class": _cal.book_class,
    "reschedule": _cal.reschedule,
    "cancel_booking": _cal.cancel_booking,
    "get_caller_history": _sheets.get_caller_history,
    "log_call": _sheets.log_call,
    "escalate_to_human": _escalate.escalate_to_human,
}


class VapiService:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    async def _load_session(self, call_id: str) -> BookingSession:
        loaded = await self.store.get(call_id)
        if loaded is None:
            return BookingSession(call_id=call_id)
        session, _ = loaded
        return session

    async def _save_session(self, session: BookingSession) -> None:
        await self.store.save(session.call_id, session, [])

    async def handle_tool_calls(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        call_id: str = (payload.get("call") or {}).get("id", "unknown")
        session = await self._load_session(call_id)

        tool_list = payload.get("toolWithToolCallList") or payload.get("toolCalls") or []
        results: list[dict[str, str]] = []

        for item in tool_list:
            tool_call = item.get("toolCall") or item
            tc_id: str = tool_call.get("id", "")
            fn = tool_call.get("function") or {}
            name: str = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")

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

        await self._save_session(session)
        return results

    async def handle_end_of_call(self, payload: dict[str, Any]) -> None:
        call_id: str = (payload.get("call") or {}).get("id", "")
        if call_id:
            await self.store.delete(call_id)
