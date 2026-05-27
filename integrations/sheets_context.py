"""Shared Google Sheets client instance for tools and scripts."""

from __future__ import annotations

from integrations.google_sheets import GoogleSheetsClient

_client: GoogleSheetsClient | None = None


def set_sheets_client(client: GoogleSheetsClient) -> None:
    global _client
    _client = client


def get_sheets_client() -> GoogleSheetsClient:
    if _client is None:
        raise RuntimeError("Google Sheets client is not initialized")
    return _client
