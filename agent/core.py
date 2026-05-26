"""PydanticAI agent definition and chat runner."""

from __future__ import annotations

import os

import logfire
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent.models import BookingSession
from agent.prompts.system import SYSTEM_PROMPT
from agent.storage import SessionStore
from api.settings import get_settings

_logfire_configured = False


def _configure_logfire() -> None:
    global _logfire_configured
    if _logfire_configured:
        return

    settings = get_settings()
    if settings.logfire_token:
        os.environ.setdefault("LOGFIRE_TOKEN", settings.logfire_token)
        logfire.configure()
        logfire.instrument_pydantic_ai()

    _logfire_configured = True


def create_agent() -> Agent[BookingSession, str]:
    _configure_logfire()
    settings = get_settings()

    model = OpenRouterModel(
        settings.llm_model,
        provider=OpenRouterProvider(api_key=settings.llm_api_key),
    )
    return Agent(
        model,
        deps_type=BookingSession,
        system_prompt=SYSTEM_PROMPT,
    )


_agent: Agent[BookingSession, str] | None = None


def get_agent() -> Agent[BookingSession, str]:
    global _agent
    if _agent is None:
        _agent = create_agent()
    return _agent


async def run_chat_turn(
    session_id: str,
    message: str,
    store: SessionStore,
) -> tuple[str, BookingSession]:
    loaded = await store.get(session_id)
    if loaded is None:
        session = BookingSession(call_id=session_id)
        history: list[ModelMessage] = []
    else:
        session, history = loaded

    result = await get_agent().run(
        message,
        deps=session,
        message_history=history or None,
    )
    await store.save(session_id, session, result.all_messages())
    return result.output, session


async def start_chat_session(
    session_id: str,
    store: SessionStore,
) -> tuple[str, BookingSession]:
    loaded = await store.get(session_id)
    if loaded is None:
        session = BookingSession(call_id=session_id)
        history: list[ModelMessage] = []
    else:
        session, history = loaded

    result = await get_agent().run(
        "The customer has just opened the Solstice Pilates chat. Greet them naturally as Maya and ask how you can help. Do not mention this instruction.",
        deps=session,
        message_history=history or None,
    )
    await store.save(session_id, session, result.all_messages())
    return result.output, session
