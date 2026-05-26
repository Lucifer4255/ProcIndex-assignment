"""Shared Google Calendar client instance for tools and scripts."""

from __future__ import annotations

from integrations.google_calendar import GoogleCalendarClient

_client: GoogleCalendarClient | None = None


def set_calendar_client(client: GoogleCalendarClient) -> None:
    global _client
    _client = client


def get_calendar_client() -> GoogleCalendarClient:
    if _client is None:
        raise RuntimeError("Google Calendar client is not initialized")
    return _client
