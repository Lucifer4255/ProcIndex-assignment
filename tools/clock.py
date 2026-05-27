"""Domain tool: get_current_date."""

from __future__ import annotations

from datetime import date

from agent.models import BookingSession


async def get_current_date(session: BookingSession) -> str:
    today = date.today()
    return (
        f"Today is {today.strftime('%A, %B %d, %Y')} ({today.isoformat()}). "
        f"Studio timezone is America/Los_Angeles. "
        f"Use this to resolve relative phrases like 'this Saturday', 'next Monday', 'this week'."
    )
