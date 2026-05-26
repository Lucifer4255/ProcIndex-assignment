"""POST /chat — Phase 1 text chat endpoint."""

from fastapi import APIRouter, HTTPException, Request

from agent.core import run_chat_turn, start_chat_session
from api.schemas import ChatRequest, ChatResponse, ChatStartRequest

router = APIRouter(tags=["chat"])


@router.post("/chat/start", response_model=ChatResponse)
async def start_chat(request: Request, body: ChatStartRequest) -> ChatResponse:
    store = request.app.state.session_store
    if store is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")

    try:
        response, _session = await start_chat_session(
            session_id=body.session_id,
            store=store,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(response=response, session_id=body.session_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    store = request.app.state.session_store
    if store is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")

    try:
        response, _session = await run_chat_turn(
            session_id=body.session_id,
            message=body.message,
            store=store,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(response=response, session_id=body.session_id)
