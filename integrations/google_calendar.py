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

CLASS_CAPACITY = {"Reformer": 10, "Mat": 15, "Tower": 8}


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

    async def find_slot_at(
        self,
        class_type: str,
        day: date,
        hour: int,
        minute: int = 0,
    ) -> ClassSlot | None:
        start = studio_datetime(day, hour, minute)
        slots = await self.list_class_slots(start, start + timedelta(minutes=1), class_type)
        for slot in slots:
            if slot.start == start:
                return slot
        return None

    async def list_slots_for_day(
        self,
        day: date,
        class_type: str | None = None,
    ) -> list[ClassSlot]:
        start, end = slot_window_for_date(day)
        return await self.list_class_slots(start, end, class_type)

    async def find_booking_by_phone(
        self,
        phone: str,
        start: datetime,
        end: datetime,
    ) -> ClassSlot | None:
        target = normalize_phone(phone)
        for slot in await self.list_class_slots(start, end):
            for booking in slot.booked:
                if normalize_phone(booking.phone) == target:
                    return slot
        return None

    async def list_bookings_by_phone(
        self,
        phone: str,
        start: datetime,
        end: datetime,
    ) -> list[ClassSlot]:
        target = normalize_phone(phone)
        matches: list[ClassSlot] = []
        for slot in await self.list_class_slots(start, end):
            if any(normalize_phone(b.phone) == target for b in slot.booked):
                matches.append(slot)
        return matches

    async def save_slot_bookings(self, slot: ClassSlot, booked: list[ClassBooking]) -> ClassSlot:
        description = build_description(slot.class_type, slot.capacity, booked)
        event = await self.patch_event(
            slot.event_id,
            {"description": description},
        )
        updated = parse_slot_from_event(event)
        if updated is None:
            raise RuntimeError(f"Failed to update bookings for event {slot.event_id}")
        return updated

    async def add_booking(self, slot: ClassSlot, name: str, phone: str) -> ClassSlot:
        phone_n = normalize_phone(phone)
        if any(normalize_phone(b.phone) == phone_n for b in slot.booked):
            return slot
        booked = [*slot.booked, ClassBooking(name=name.strip(), phone=phone_n)]
        if len(booked) > slot.capacity:
            raise ValueError("Class is full")
        return await self.save_slot_bookings(slot, booked)

    async def remove_booking(self, slot: ClassSlot, phone: str) -> ClassSlot:
        phone_n = normalize_phone(phone)
        booked = [b for b in slot.booked if normalize_phone(b.phone) != phone_n]
        return await self.save_slot_bookings(slot, booked)

    async def move_booking(
        self,
        phone: str,
        target_slot: ClassSlot,
        name: str | None = None,
    ) -> ClassSlot:
        phone_n = normalize_phone(phone)
        search_start = studio_datetime(date.today(), 0)
        search_end = search_start + timedelta(days=60)
        current = await self.find_booking_by_phone(phone_n, search_start, search_end)
        if current and current.event_id != target_slot.event_id:
            await self.remove_booking(current, phone_n)

        display_name = name
        if not display_name and current:
            for booking in current.booked:
                if normalize_phone(booking.phone) == phone_n:
                    display_name = booking.name
                    break
        if not display_name:
            display_name = "Guest"

        return await self.add_booking(target_slot, display_name, phone_n)


def studio_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=STUDIO_TZ)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone.strip()


def is_valid_phone(phone: str) -> bool:
    """True if the input contains a US-shaped phone (10 digits, or 11 starting with 1)."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return True
    if len(digits) == 11 and digits.startswith("1"):
        return True
    return False


def parse_time_string(value: str) -> tuple[int, int] | None:
    raw = value.strip().lower().replace(" ", "")
    if not raw:
        return None

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None

    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        suffix = match.group(3)
        if hour < 1 or hour > 12 or minute > 59:
            return None
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return hour, minute

    return None


def slot_window_for_date(day: date) -> tuple[datetime, datetime]:
    start = studio_datetime(day, 0)
    end = studio_datetime(day, 23, 59)
    return start, end


def format_slot_line(slot: ClassSlot) -> str:
    day_label = f"{slot.start.strftime('%A %b')} {slot.start.day}"
    time_label = format_time_label(slot.start)
    if slot.is_full:
        spots = "full"
    elif slot.remaining_spots == 1:
        spots = "1 spot left"
    else:
        spots = f"{slot.remaining_spots} spots left"
    return f"{slot.class_type} {time_label} on {day_label} ({spots})"


def create_calendar_client_from_env() -> GoogleCalendarClient:
    from api.settings import get_settings

    settings = get_settings()
    return GoogleCalendarClient(
        service_account_json=settings.google_service_account_json,
        calendar_id=settings.google_calendar_id,
    )
