"""Google Sheets async wrapper for Contacts caller log."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from integrations.google_calendar import normalize_phone

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CONTACTS_TAB = "Contacts"
CONTACTS_RANGE = f"{CONTACTS_TAB}!A:J"

CALLLOG_TAB = "CallLog"
CALLLOG_RANGE = f"{CALLLOG_TAB}!A:H"

COLUMNS = (
    "phone",
    "name",
    "last_called",
    "call_count",
    "last_class_booked",
    "last_call_reason",
    "last_call_summary",
    "priority_flag",
    "callback_required",
    "notes",
)

CALLLOG_COLUMNS = (
    "timestamp",
    "phone",
    "name",
    "reason",
    "summary",
    "priority",
    "callback_required",
    "notes",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: list[str]) -> dict[str, Any]:
    padded = row + [""] * (len(COLUMNS) - len(row))
    data = dict(zip(COLUMNS, padded[: len(COLUMNS)], strict=False))
    if data.get("call_count"):
        try:
            data["call_count"] = int(str(data["call_count"]).strip())
        except ValueError:
            data["call_count"] = 0
    return data


def _dict_to_row(data: dict[str, Any]) -> list[str]:
    return [str(data.get(col, "") or "") for col in COLUMNS]


class GoogleSheetsClient:
    """Sync Google Sheets API wrapped for async callers."""

    def __init__(self, service_account_json: str, sheet_id: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_json,
            scopes=SCOPES,
        )
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._sheet_id = sheet_id

    async def _run(self, fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    async def get_rows(self) -> list[list[str]]:
        def _get():
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self._sheet_id, range=CONTACTS_RANGE)
                .execute()
            )
            return result.get("values", [])

        return await self._run(_get)

    async def get_row_by_phone(self, phone: str) -> dict[str, Any] | None:
        target = normalize_phone(phone)
        rows = await self.get_rows()
        if not rows:
            return None

        start = 1
        if rows[0] and rows[0][0].lower() == "phone":
            start = 1
        else:
            start = 0

        for index, row in enumerate(rows[start:], start=start + 1):
            if not row:
                continue
            row_phone = normalize_phone(row[0]) if row[0] else ""
            if row_phone == target:
                data = _row_to_dict(row)
                data["_row_index"] = index
                return data
        return None

    async def upsert_row(
        self,
        phone: str,
        fields: dict[str, Any],
        *,
        increment_call_count: bool = True,
    ) -> dict[str, Any]:
        phone_n = normalize_phone(phone)
        existing = await self.get_row_by_phone(phone_n)

        merged: dict[str, Any] = {
            "phone": phone_n,
            "name": "",
            "last_called": _now_iso(),
            "call_count": 1,
            "last_class_booked": "",
            "last_call_reason": "",
            "last_call_summary": "",
            "priority_flag": "normal",
            "callback_required": "FALSE",
            "notes": "",
        }

        if existing:
            merged.update({k: v for k, v in existing.items() if not str(k).startswith("_")})
            if increment_call_count:
                merged["call_count"] = int(merged.get("call_count") or 0) + 1
            merged["last_called"] = _now_iso()

        merged.update(fields)
        merged["phone"] = phone_n

        if isinstance(merged.get("callback_required"), bool):
            merged["callback_required"] = "TRUE" if merged["callback_required"] else "FALSE"

        row_values = _dict_to_row(merged)

        if existing and "_row_index" in existing:
            row_index = existing["_row_index"]

            def _update():
                range_name = f"{CONTACTS_TAB}!A{row_index}:J{row_index}"
                body = {"values": [row_values]}
                self._service.spreadsheets().values().update(
                    spreadsheetId=self._sheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body=body,
                ).execute()

            await self._run(_update)
        else:

            def _append():
                body = {"values": [row_values]}
                self._service.spreadsheets().values().append(
                    spreadsheetId=self._sheet_id,
                    range=CONTACTS_RANGE,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                ).execute()

            await self._run(_append)

        return merged


    async def append_log_row(
        self,
        phone: str,
        fields: dict[str, Any],
    ) -> None:
        """Append one row to the CallLog tab — full per-event history.

        Unlike Contacts.upsert_row, this never overwrites prior rows: every call
        gets its own audit entry so urgent issues aren't masked by later actions.
        """
        phone_n = normalize_phone(phone)
        row_dict: dict[str, Any] = {
            "timestamp": _now_iso(),
            "phone": phone_n,
            "name": fields.get("name", ""),
            "reason": fields.get("reason", ""),
            "summary": fields.get("summary", ""),
            "priority": fields.get("priority", "normal"),
            "callback_required": fields.get("callback_required", "FALSE"),
            "notes": fields.get("notes", ""),
        }
        if isinstance(row_dict["callback_required"], bool):
            row_dict["callback_required"] = "TRUE" if row_dict["callback_required"] else "FALSE"
        row_values = [str(row_dict.get(col, "") or "") for col in CALLLOG_COLUMNS]

        def _append():
            body = {"values": [row_values]}
            self._service.spreadsheets().values().append(
                spreadsheetId=self._sheet_id,
                range=CALLLOG_RANGE,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()

        await self._run(_append)


def create_sheets_client_from_env() -> GoogleSheetsClient:
    from api.settings import get_settings

    settings = get_settings()
    if not settings.google_sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not configured")
    return GoogleSheetsClient(
        service_account_json=settings.google_service_account_json,
        sheet_id=settings.google_sheet_id,
    )
