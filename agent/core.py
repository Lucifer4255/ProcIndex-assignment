"""PydanticAI agent definition and chat runner."""

from __future__ import annotations

import importlib
import os

import logfire
from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent.models import BookingSession
from agent.prompts.system import SYSTEM_PROMPT
from agent.storage import SessionStore
from api.settings import get_settings

load_dotenv()

_settings = get_settings()

# Always configure logfire — falls back to CLI credentials if no token is set.
if _settings.logfire_token:
    os.environ.setdefault("LOGFIRE_TOKEN", _settings.logfire_token)
logfire.configure()
logfire.instrument_pydantic_ai()

# Underscore prefix avoids clashing with the `agent` package name.
_agent: Agent[BookingSession, str] = Agent(
    OpenRouterModel(
        _settings.llm_model,
        provider=OpenRouterProvider(api_key=_settings.llm_api_key),
    ),
    deps_type=BookingSession,
    instructions=SYSTEM_PROMPT,
)

# importlib avoids rebinding the `_agent` name in this module.
importlib.import_module("agent.tools.calendar")


def get_agent() -> Agent[BookingSession, str]:
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

    user_prompt = message.strip()
    run_instructions: str | None = None

    if not history and not user_prompt:
        user_prompt = "Hello"
        run_instructions = (
            "The customer just opened the Solstice Pilates chat. "
            "Greet them naturally as Maya and ask how you can help."
        )

    result = await _agent.run(
        user_prompt,
        deps=session,
        message_history=history or None,
        instructions=run_instructions,
    )
    await store.save(session_id, session, result.all_messages())
    return result.output, session
