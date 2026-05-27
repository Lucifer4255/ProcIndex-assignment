"""End-to-end test: drive the agent through a real conversation, verify state."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agent.core import run_chat_turn
from agent.storage import SessionStore
from api.settings import get_settings
from integrations.calendar_context import set_calendar_client
from integrations.google_calendar import (
    GoogleCalendarClient,
    format_slot_line,
    normalize_phone,
    studio_datetime,
)
from integrations.google_sheets import (
    CALLLOG_RANGE,
    GoogleSheetsClient,
)
from integrations.sheets_context import set_sheets_client

TEST_PHONE = "+14155551234"
TEST_NAME = "EndToEnd Test"


def banner(label: str) -> None:
    print(f"\n{'='*70}\n  {label}\n{'='*70}")


async def chat(store: SessionStore, session_id: str, message: str) -> str:
    print(f"\n→ User: {message}")
    reply, _ = await run_chat_turn(session_id=session_id, message=message, store=store)
    print(f"← Maya: {reply}")
    return reply


async def main() -> None:
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    store = SessionStore(redis_client)

    cal = GoogleCalendarClient(
        service_account_json=settings.google_service_account_json,
        calendar_id=settings.google_calendar_id,
    )
    set_calendar_client(cal)

    sheets = GoogleSheetsClient(
        service_account_json=settings.google_service_account_json,
        sheet_id=settings.google_sheet_id,
    )
    set_sheets_client(sheets)

    # Clean any prior test rows so we observe only this run's writes
    contacts = await sheets.get_row_by_phone(TEST_PHONE)
    if contacts:
        print(f"(prior Contacts row exists for {TEST_PHONE} — call_count={contacts.get('call_count')})")

    thu = date(2026, 5, 28)
    fri = thu + timedelta(days=1)
    session_id = f"e2e-{uuid.uuid4()}"

    banner("FLOW 1 — fresh booking")
    await chat(store, session_id, "Hi, can I book a 7pm Reformer this Thursday?")
    await chat(store, session_id, "Yes")
    await chat(store, session_id, TEST_NAME)
    await chat(store, session_id, TEST_PHONE)

    banner("VERIFY 1 — Thu 7pm Reformer should contain our booking")
    slot_dt = studio_datetime(thu, 19)
    slots = await cal.list_class_slots(slot_dt, slot_dt + timedelta(minutes=1))
    for s in slots:
        booked_phones = [normalize_phone(b.phone) for b in s.booked]
        present = normalize_phone(TEST_PHONE) in booked_phones
        print(f"  {format_slot_line(s)} -> our phone present: {present}")

    banner("FLOW 2 — reschedule to Friday 7pm (same session, name/phone reused)")
    await chat(store, session_id, "Actually can you move it to Friday 7pm instead?")

    banner("VERIFY 2 — Thu 7pm should no longer have us, Fri 7pm should")
    thu_slots = await cal.list_class_slots(slot_dt, slot_dt + timedelta(minutes=1))
    for s in thu_slots:
        present = normalize_phone(TEST_PHONE) in [normalize_phone(b.phone) for b in s.booked]
        print(f"  Thu: {format_slot_line(s)} -> still present: {present}")
    fri_dt = studio_datetime(fri, 19)
    fri_slots = await cal.list_class_slots(fri_dt, fri_dt + timedelta(minutes=1))
    for s in fri_slots:
        present = normalize_phone(TEST_PHONE) in [normalize_phone(b.phone) for b in s.booked]
        print(f"  Fri: {format_slot_line(s)} -> now present: {present}")

    banner("FLOW 3 — escalation (billing) in fresh session")
    esc_session = f"e2e-esc-{uuid.uuid4()}"
    await chat(store, esc_session, f"Hi I was double-charged for my last class, my name is {TEST_NAME}")
    await chat(store, esc_session, TEST_PHONE)

    banner("VERIFY 3 — Sheets state")
    row = await sheets.get_row_by_phone(TEST_PHONE)
    if row:
        print(f"  Contacts row: name={row.get('name')!r} priority_flag={row.get('priority_flag')!r} "
              f"callback_required={row.get('callback_required')!r} call_count={row.get('call_count')!r}")
        print(f"               last_call_reason={row.get('last_call_reason')!r}")
        print(f"               last_class_booked={row.get('last_class_booked')!r}")
    else:
        print("  Contacts row NOT FOUND (unexpected)")

    # CallLog — fetch all rows and filter by phone
    raw = await sheets._run(
        lambda: sheets._service.spreadsheets()
        .values()
        .get(spreadsheetId=sheets._sheet_id, range=CALLLOG_RANGE)
        .execute()
    )
    rows = raw.get("values", [])
    phone_target = normalize_phone(TEST_PHONE)
    matching = [
        r for r in rows[1:]  # skip header
        if len(r) > 1 and normalize_phone(r[1] if len(r) > 1 else "") == phone_target
    ]
    print(f"  CallLog rows for {phone_target}: {len(matching)} (expect 4: book + reschedule + escalation, may vary)")
    for r in matching:
        ts = r[0] if len(r) > 0 else ""
        reason = r[3] if len(r) > 3 else ""
        priority = r[5] if len(r) > 5 else ""
        print(f"    {ts} | reason={reason} | priority={priority}")

    await redis_client.aclose()
    banner("E2E COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
