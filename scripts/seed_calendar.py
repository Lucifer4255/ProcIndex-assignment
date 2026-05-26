"""Seed Solstice Pilates class slots and sample bookings into Google Calendar."""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from integrations.google_calendar import (
    ClassBooking,
    GoogleCalendarClient,
    create_calendar_client_from_env,
    studio_datetime,
)

load_dotenv(ROOT / ".env")

CLASS_TYPES = ("Reformer", "Mat", "Tower")
CAPACITY = {"Reformer": 10, "Mat": 15, "Tower": 8}
SLOT_TIMES = (6, 9, 18, 19)  # 6am, 9am, 6pm, 7pm
WEEKS = 2


def monday_of_current_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def class_type_for_slot(day_index: int, time_index: int) -> str:
    return CLASS_TYPES[(day_index * len(SLOT_TIMES) + time_index) % len(CLASS_TYPES)]


def placeholder_bookings(count: int, prefix: str) -> list[ClassBooking]:
    return [
        ClassBooking(name=f"{prefix} {i}", phone=f"+1415555{1000 + i:04d}")
        for i in range(1, count + 1)
    ]


async def seed() -> None:
    client = create_calendar_client_from_env()
    today = date.today()
    start_day = monday_of_current_week(today)
    end_day = start_day + timedelta(days=WEEKS * 7)

    range_start = studio_datetime(start_day, 0)
    range_end = studio_datetime(end_day, 23, 59)

    print(f"Clearing existing class slots from {start_day} to {end_day}...")
    deleted = await client.delete_class_slots_in_range(range_start, range_end)
    print(f"Deleted {deleted} existing class slot events.")

    thursday_6pm: datetime | None = None
    thursday_7pm: datetime | None = None
    friday_9am: datetime | None = None
    created = 0

    for week in range(WEEKS):
        for weekday in range(6):  # Mon–Sat
            day = start_day + timedelta(days=week * 7 + weekday)
            day_index = week * 6 + weekday
            for time_index, hour in enumerate(SLOT_TIMES):
                class_type = class_type_for_slot(day_index, time_index)
                start = studio_datetime(day, hour)
                booked: list[ClassBooking] | None = None

                if weekday == 3 and hour == 18:
                    class_type = "Reformer"
                    thursday_6pm = start
                    booked = placeholder_bookings(CAPACITY["Reformer"], "Thu6")
                elif weekday == 3 and hour == 19:
                    class_type = "Reformer"
                    thursday_7pm = start
                    booked = placeholder_bookings(8, "Thu7")
                elif weekday == 4 and hour == 9:
                    class_type = "Mat"
                    friday_9am = start
                    booked = [ClassBooking(name="Mike", phone="+14155550191")]

                await client.upsert_class_slot(
                    class_type=class_type,
                    start=start,
                    duration_minutes=60,
                    capacity=CAPACITY[class_type],
                    booked=booked,
                )
                created += 1

    print(f"Created {created} class slot events.")

    if thursday_6pm:
        slot = (await client.list_class_slots(thursday_6pm, thursday_6pm + timedelta(minutes=1)))[0]
        print(f"Thursday 6pm {slot.class_type}: {slot.remaining_spots} spots (expect 0)")

    if thursday_7pm:
        slot = (await client.list_class_slots(thursday_7pm, thursday_7pm + timedelta(minutes=1)))[0]
        print(f"Thursday 7pm {slot.class_type}: {slot.remaining_spots} spots (expect 2)")

    if friday_9am:
        slot = (await client.list_class_slots(friday_9am, friday_9am + timedelta(minutes=1)))[0]
        print(
            f"Friday 9am {slot.class_type}: booked={[b.name for b in slot.booked]} "
            f"(expect Mike)"
        )

    print("Seed complete. Check Google Calendar UI.")


if __name__ == "__main__":
    asyncio.run(seed())
