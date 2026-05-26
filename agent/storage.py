"""Redis-backed persistence for active chat/call sessions."""

from __future__ import annotations

import json

import redis.asyncio as redis
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from agent.models import BookingSession

SESSION_TTL_SECONDS = 3600


class SessionStore:
    """Stores BookingSession plus PydanticAI message history in Redis."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def get(self, session_id: str) -> tuple[BookingSession, list[ModelMessage]] | None:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None

        payload = json.loads(raw)
        session = BookingSession.from_dict(payload["session"])
        if not session.call_id:
            session.call_id = session_id

        history: list[ModelMessage] = []
        history_json = payload.get("message_history")
        if history_json:
            history = ModelMessagesTypeAdapter.validate_json(history_json.encode())

        return session, history

    async def save(
        self,
        session_id: str,
        session: BookingSession,
        history: list[ModelMessage],
    ) -> None:
        if not session.call_id:
            session.call_id = session_id

        payload = {
            "session": session.to_dict(),
            "message_history": ModelMessagesTypeAdapter.dump_json(history).decode(),
        }
        await self._redis.set(
            self._key(session_id),
            json.dumps(payload),
            ex=SESSION_TTL_SECONDS,
        )

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))
