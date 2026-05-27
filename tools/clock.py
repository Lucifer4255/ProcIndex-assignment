"""Domain tool: get_current_date."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.models import BookingSession

_STUDIO_TZ = ZoneInfo("America/Los_Angeles")


async def get_current_date(session: BookingSession) -> str:
    now = datetime.now(tz=_STUDIO_TZ)
    return (
        f"Today is {now.strftime('%A, %B %d, %Y')} ({now.date().isoformat()}), "
        f"{now.strftime('%I:%M %p')} PST. "
        f"Studio timezone is America/Los_Angeles. "
        f"Use this to resolve relative phrases like 'this Saturday', 'next Monday', 'this week'."
    )
