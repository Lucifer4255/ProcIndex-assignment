"""PydanticAI agent definition and chat runner."""

from __future__ import annotations

import importlib
import os
from datetime import date

import logfire
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

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

def _or_model(model_id: str) -> OpenRouterModel:
    return OpenRouterModel(model_id, provider=OpenRouterProvider(api_key=_settings.llm_api_key))


# FallbackModel tries each in order — moves to next only on error.
# Underscore prefix avoids clashing with the `agent` package name.
_agent: Agent[BookingSession, str] = Agent(
    FallbackModel(
        _or_model("deepseek/deepseek-v3.2"),    # fallback
        _or_model("deepseek/deepseek-v4-flash"),      # fastest + cheapest
        _or_model("z-ai/glm-4.5-air"),      # final — reliable tool calling
    ),
    deps_type=BookingSession,
    instructions=SYSTEM_PROMPT,
)

@_agent.instructions
def runtime_context(ctx: RunContext[BookingSession]) -> str:
    """Inject server-controlled runtime facts (date, timezone) so the model can
    resolve relative phrases like 'this Thursday'. No user input is injected here —
    caller-supplied fields stay in BookingSession deps and are read via tools."""
    today = date.today()
    return (
        f"Today is {today.strftime('%A, %B %d, %Y')} ({today.isoformat()}). "
        f"Studio timezone is America/Los_Angeles. "
        f"When a caller says 'this Thursday' or 'next Monday', resolve it against today's date."
    )


@_agent.instructions
def session_snapshot(ctx: RunContext[BookingSession]) -> str:
    """Surface what's already been collected about the caller so the model
    can reuse fields (per the WORKFLOW reuse rule for book_class) without
    introspecting deps via a tool. Only includes non-empty fields."""
    s = ctx.deps
    known: list[str] = []
    if s.caller_name:
        known.append(f"name={s.caller_name}")
    if s.caller_phone:
        known.append(f"phone={s.caller_phone}")
    if s.class_type:
        known.append(f"class_type={s.class_type}")
    if s.preferred_date:
        known.append(f"date={s.preferred_date}")
    if s.preferred_time:
        known.append(f"time={s.preferred_time}")
    if s.existing_bookings:
        bookings = "; ".join(
            f"{b.get('class_type')} {b.get('time')} on {b.get('date')}"
            for b in s.existing_bookings
        )
        known.append(f"existing_bookings=[{bookings}]")
    if not known:
        return ""
    return (
        "Known about this caller from earlier in the conversation: "
        + ", ".join(known)
        + ". Reuse these fields when calling tools (for book_class) without re-asking; "
        + "for group_booking and escalations, still confirm name+phone with the caller."
    )


# importlib avoids rebinding the `_agent` name in this module.
importlib.import_module("agent.tools.calendar")
importlib.import_module("agent.tools.sheets")
importlib.import_module("agent.tools.escalate")


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

    user_prompt = message.strip() or "Hello"

    result = await _agent.run(
        user_prompt,
        deps=session,
        message_history=history or None,
        usage_limits=UsageLimits(tool_calls_limit=10),
    )
    await store.save(session_id, session, result.all_messages())
    return result.output, session
