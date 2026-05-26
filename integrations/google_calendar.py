"""Google Calendar async wrapper for class slot events."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLASS_MARKER = "CLASS_SLOT"
STUDIO_LOCATION = "Solstice Pilates, San Francisco"
STUDIO_TZ = ZoneInfo("America/Los_Angeles")

BOOKING_PATTERN = re.compile(
    r"([^,(]+?)\s*\(\s*(\+?\d[\d\s\-()]+)\s*\)",
)


@dataclass(frozen=True)
class ClassBooking:
    name: str
    phone: str


@dataclass
class ClassSlot:
    event_id: str
    summary: str
    class_type: str
    start: datetime
    end: datetime
    capacity: int
    booked: list[ClassBooking]
    location: str = STUDIO_LOCATION
    raw_description: str = ""

    @property
    def remaining_spots(self) -> int:
        return max(0, self.capacity - len(self.booked))

    @property
    def is_full(self) -> bool:
        return self.remaining_spots == 0


def format_time_label(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    if dt.minute:
        return f"{hour}:{dt.minute:02d}{suffix}"
    return f"{hour}{suffix}"


def build_summary(class_type: str, start: datetime) -> str:
    return f"{class_type} Class — {format_time_label(start)}"


def build_description(
    class_type: str,
    capacity: int,
    booked: list[ClassBooking] | None = None,
) -> str:
    booked = booked or []
    booked_text = ", ".join(f"{b.name} ({b.phone})" for b in booked)
    if not booked_text:
        booked_text = ""
    return (
        f"{CLASS_MARKER} | Type: {class_type} | Capacity: {capacity} | "
        f"Booked: {booked_text}"
    ).strip()


def parse_description(description: str) -> tuple[str | None, int | None, list[ClassBooking]]:
    if CLASS_MARKER not in description:
        return None, None, []

    class_type: str | None = None
    capacity: int | None = None
    booked: list[ClassBooking] = []

    type_match = re.search(r"Type:\s*([^|]+)", description)
    if type_match:
        class_type = type_match.group(1).strip()

    cap_match = re.search(r"Capacity:\s*(\d+)", description)
    if cap_match:
        capacity = int(cap_match.group(1))

    booked_match = re.search(r"Booked:\s*(.*)$", description, re.DOTALL)
    if booked_match:
        booked_text = booked_match.group(1).strip()
        if booked_text:
            for name, phone in BOOKING_PATTERN.findall(booked_text):
                booked.append(
                    ClassBooking(
                        name=name.strip(),
                        phone=phone.strip(),
                    )
                )

    return class_type, capacity, booked


def _parse_event_datetime(value: dict[str, str]) -> datetime:
    if "dateTime" in value:
        raw = value["dateTime"]
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(STUDIO_TZ)
        return datetime.fromisoformat(raw).astimezone(STUDIO_TZ)
    day = date.fromisoformat(value["date"])
    return datetime.combine(day, time.min, tzinfo=STUDIO_TZ)


def parse_slot_from_event(event: dict[str, Any]) -> ClassSlot | None:
    description = event.get("description", "")
    class_type, capacity, booked = parse_description(description)
    if class_type is None or capacity is None:
        return None

    start = _parse_event_datetime(event["start"])
    end = _parse_event_datetime(event["end"])
    return ClassSlot(
        event_id=event["id"],
        summary=event.get("summary", build_summary(class_type, start)),
        class_type=class_type,
        start=start,
        end=end,
        capacity=capacity,
        booked=booked,
        location=event.get("location", STUDIO_LOCATION),
        raw_description=description,
    )


class GoogleCalendarClient:
    """Sync Google Calendar API wrapped for async callers."""

    def __init__(self, service_account_json: str, calendar_id: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_json,
            scopes=SCOPES,
        )
        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self._calendar_id = calendar_id

    async def _run(self, fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    async def list_events(
        self,
        start: datetime,
        end: datetime,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        def _list():
            params: dict[str, Any] = {
                "calendarId": self._calendar_id,
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if query:
                params["q"] = query

            events: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                if page_token:
                    params["pageToken"] = page_token
                else:
                    params.pop("pageToken", None)
                result = self._service.events().list(**params).execute()
                events.extend(result.get("items", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            return events

        return await self._run(_list)

    async def insert_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str,
        location: str = STUDIO_LOCATION,
    ) -> dict[str, Any]:
        body = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": str(STUDIO_TZ)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(STUDIO_TZ)},
        }

        def _insert():
            return (
                self._service.events()
                .insert(calendarId=self._calendar_id, body=body)
                .execute()
            )

        return await self._run(_insert)

    async def patch_event(self, event_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        def _patch():
            return (
                self._service.events()
                .patch(calendarId=self._calendar_id, eventId=event_id, body=updates)
                .execute()
            )

        return await self._run(_patch)

    async def delete_event(self, event_id: str) -> None:
        def _delete():
            self._service.events().delete(
                calendarId=self._calendar_id,
                eventId=event_id,
            ).execute()

        await self._run(_delete)

    async def list_class_slots(
        self,
        start: datetime,
        end: datetime,
        class_type: str | None = None,
    ) -> list[ClassSlot]:
        events = await self.list_events(start, end, query=CLASS_MARKER)
        slots: list[ClassSlot] = []
        for event in events:
            slot = parse_slot_from_event(event)
            if slot is None:
                continue
            if class_type and slot.class_type.lower() != class_type.lower():
                continue
            slots.append(slot)
        return sorted(slots, key=lambda s: s.start)

    async def delete_class_slots_in_range(self, start: datetime, end: datetime) -> int:
        events = await self.list_events(start, end, query=CLASS_MARKER)
        deleted = 0
        for event in events:
            await self.delete_event(event["id"])
            deleted += 1
        return deleted

    async def upsert_class_slot(
        self,
        class_type: str,
        start: datetime,
        duration_minutes: int,
        capacity: int,
        booked: list[ClassBooking] | None = None,
    ) -> ClassSlot:
        end = start + timedelta(minutes=duration_minutes)
        description = build_description(class_type, capacity, booked)
        summary = build_summary(class_type, start)

        existing = await self.list_class_slots(start, start + timedelta(minutes=1), class_type)
        matching = [s for s in existing if s.start == start]
        if matching:
            event = await self.patch_event(
                matching[0].event_id,
                {
                    "summary": summary,
                    "description": description,
                    "location": STUDIO_LOCATION,
                    "start": {"dateTime": start.isoformat(), "timeZone": str(STUDIO_TZ)},
                    "end": {"dateTime": end.isoformat(), "timeZone": str(STUDIO_TZ)},
                },
            )
        else:
            event = await self.insert_event(summary, start, end, description)

        slot = parse_slot_from_event(event)
        if slot is None:
            raise RuntimeError(f"Failed to parse seeded event for {class_type} at {start}")
        return slot


def studio_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=STUDIO_TZ)


def create_calendar_client_from_env() -> GoogleCalendarClient:
    from api.settings import get_settings

    settings = get_settings()
    return GoogleCalendarClient(
        service_account_json=settings.google_service_account_json,
        calendar_id=settings.google_calendar_id,
    )
