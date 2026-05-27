"""FastAPI router for Vapi webhook — HTTP boundary only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent.storage import SessionStore
from vapi_adapter.service import VapiService

router = APIRouter(prefix="/vapi")


def _get_service(request: Request) -> VapiService:
    store: SessionStore = request.app.state.session_store
    return VapiService(store)


@router.post("/webhook")
async def vapi_webhook(request: Request) -> JSONResponse:
    """Receive Vapi server messages and respond with tool results."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({}, status_code=200)

    msg: dict[str, Any] = body.get("message") or body
    msg_type: str = msg.get("type", "")
    service = _get_service(request)

    if msg_type == "tool-calls":
        results = await service.handle_tool_calls(msg)
        return JSONResponse({"results": results})

    if msg_type == "end-of-call-report":
        await service.handle_end_of_call(msg)
        return JSONResponse({})

    return JSONResponse({})
