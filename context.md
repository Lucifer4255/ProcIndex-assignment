# Context for Claude Code — Solstice Pilates Receptionist

> Read this entire file before writing any code. All major decisions are already made. Do not relitigate them.

---

## What This Is

An AI receptionist for a pilates studio. Two phases:
- **Phase 1:** Text chat UI backed by a PydanticAI agent
- **Phase 2:** Same agent wired into Vapi for live voice calls

The full architecture is in `architecture.md`. This file tells you how to execute it.

---

## Decisions Already Made — Do Not Change

| Decision | Choice | Do not switch to |
|---|---|---|
| Language | Python 3.12+ | TypeScript |
| Agent framework | PydanticAI v1.x | LangChain, raw Anthropic SDK, Vercel AI SDK |
| Web server | FastAPI + uvicorn | Flask, Django |
| Session store | Redis | In-memory dict, SQLite |
| LLM | Configurable via env; OpenRouter recommended for dev | Hardcoded provider/model |
| Calendar | google-api-python-client (sync, wrapped in executor) | gspread alternatives |
| Observability | Logfire | LangSmith, Langfuse |
| Voice platform | Vapi | Retell, Twilio directly, LiveKit |
| UI | Plain HTML/JS | React, Next.js, Vue |

---

## The Core Pattern — Elicitation via RunContext

The most important design pattern in this codebase. When a caller says "I want to book a class" without giving details, the agent must ask progressively — one question at a time — until it has everything it needs.

This is handled via `BookingSession` injected as PydanticAI `deps`:

```python
@dataclass
class BookingSession:
    class_type: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    caller_name: str | None = None
    caller_phone: str | None = None
    call_id: str = ""
    caller_history: dict | None = None
    slot_confirmed: bool = False
    booking_confirmed: bool = False

    def missing_for_booking(self) -> list[str]: ...
    def is_bookable(self) -> bool: ...
```

Tools read from and write to `ctx.deps`. The session persists in Redis between webhook calls keyed by `call.id`.

**Never** design tools that require all params upfront. Always merge what the LLM extracted into the session and check `is_bookable()`.

---

## Google API Pattern — Always Use Executor

The Google API client (`google-api-python-client`) is synchronous. FastAPI is async. Never call it directly in an async route or tool — it blocks the event loop.

```python
import asyncio
from functools import partial

async def async_gcal_call(service, *args, **kwargs):
    loop = asyncio.get_event_loop()
    fn = partial(service.events().insert(*args, **kwargs).execute)
    return await loop.run_in_executor(None, fn)
```

Create a single shared service instance at startup via FastAPI lifespan. Pass it through dependency injection or module-level singleton.

Google Calendar does not manage class capacity for us. Treat Calendar events as scheduled class slots, store capacity and booked callers in the event metadata/description, and compute remaining spots in our own integration code. Do not rely on `freebusy` to determine whether a class is full.

---

## Vapi Webhook — Critical Rules

1. **Always return HTTP 200** — any other status code is silently ignored by Vapi
2. **Result must be a plain string** — no dicts, no lists, no nested objects
3. **No newline characters** in the result string — use `. ` as separator instead
4. **toolCallId must exactly match** the ID from the incoming request
5. **Response shape is non-negotiable:**

```python
return {
    "results": [
        {
            "toolCallId": tool_call_id,  # from request
            "result": "plain string result here"
        }
    ]
}
```

Handle errors by returning a user-friendly string as the result — never raise HTTP exceptions from the webhook route.

---

## Session Flow Per Webhook Call

```
1. Vapi sends POST /vapi/webhook
2. Extract call.id and caller phone from payload
3. Load BookingSession + message_history from Redis
4. If first call: load caller_history from Sheets (get_caller_history)
5. Run agent with loaded session as deps, loaded history as message_history
6. Save updated session + result.all_messages() back to Redis
7. Return tool result to Vapi
```

For the `/chat` (Phase 1) route, use a `session_id` from the request body instead of `call.id`. Same Redis pattern.

---

## System Prompt Rules — Voice-First

The LLM speaks aloud in Phase 2. These rules are mandatory in the system prompt:

- Max 2 sentences per response
- One question per turn — never ask two things at once
- No bullet points, numbered lists, or markdown
- Spell out all numbers: "four one five" not "415"
- Natural fillers before tool calls: "Let me check that for you..."
- Never say: "Certainly!", "Absolutely!", "Great question!", "I'd be happy to help"
- Never mention being an AI
- Greet returning callers by name (use caller_history)

---

## Google Sheets — Upsert Pattern

The Contacts tab uses phone as the primary key. Always upsert, never insert blindly:

```
1. Read all rows to find matching phone
2. If found: update the row in place (PATCH)
3. If not found: append new row
```

Columns (in order): `phone | name | last_called | call_count | last_class_booked | last_call_reason | last_call_summary | priority_flag | callback_required | notes`

---

## Seed Data Required Before Testing

Run `scripts/seed_calendar.py` before any agent testing. It creates:
- Recurring class slots: Mon–Sat at 6am, 9am, 6pm, 7pm for 2 weeks
- Class types: Reformer (cap 10), Mat (cap 15), Tower (cap 8)
- 3 existing bookings: Sara (Reformer 7pm Thu), Mike (Mat 9am Fri), one fully booked class
- One full class to trigger the "slot full → offer alternative" path

---

## Phase 1 Build Order

Do these in sequence. Do not jump ahead.

### Step 1 — Agent hello world
Build `BookingSession`, `SessionStore`, the system prompt, PydanticAI agent, FastAPI `/chat` route, and minimal chat UI before any Google complexity.
Goal: verify the agent talks, Redis session persistence works, and the UI can send messages end-to-end.

### Step 2 — Google Calendar integration + seed
Build `integrations/google_calendar.py` and `scripts/seed_calendar.py`.
Calendar calls must use `run_in_executor`.
Treat Calendar events as class slots; parse our stored capacity/bookings to compute remaining spots.

### Step 3 — Calendar tools
Build `agent/tools/calendar.py`.
Register `check_slot`, `book_class`, `reschedule`, and `cancel_booking` on the agent.
Test the full booking flow against Google Calendar before adding Sheets.

### Step 4 — Google Sheets integration + sheets/escalation tools
Build `integrations/google_sheets.py`, `agent/tools/sheets.py`, and `agent/tools/escalate.py`.
Use phone as the primary key and always upsert Contacts rows.

### Step 5 — Phase 1 demo
Fresh seed data, run the scripted booking flow, and record Calendar + Sheets updating alongside the chat UI.

---

## Phase 2 Build Order

Only start Phase 2 after Phase 1 demo is recorded.

### Step 1 — Vapi webhook route
Build `api/routes/vapi.py`.
Handle `tool-calls` and `end-of-call-report` event types.
Reuse the exact same tools from Phase 1 — no duplication.

### Step 2 — Vapi assistant setup
Build `vapi/setup.py`.
Creates the Vapi assistant via REST API with system prompt, tools, voice config.
Tools point to `{BASE_URL}/vapi/webhook`.
Run once: `python vapi/setup.py`

### Step 3 — Voice prompt tuning
Tune the system prompt for spoken output.
Test via Vapi dashboard web call (no phone needed).
Iterate until responses sound natural and tool calls feel fast.

---

## What the Demo Videos Need to Show

### Phase 1 video
- Open the chat UI
- Ask about availability for a specific class
- Get redirected when it's full
- Complete a full booking (name + phone collected progressively)
- Show the Google Calendar event created
- Show the Google Sheets Contacts tab updated

### Phase 2 video
- Make a real call to the Vapi number (web call from dashboard or Jio international call)
- Same booking flow as Phase 1 but via voice
- Calendar and Sheets must update in real time while the call is happening
- Keep it under 2 minutes

---

## Common Mistakes to Avoid

- **Blocking the event loop** — always use `run_in_executor` for Google API calls
- **Returning non-200 from Vapi webhook** — Vapi silently ignores it, very hard to debug
- **Newlines in Vapi result strings** — use `. ` as separator, never `\n`
- **Asking multiple questions at once** — system prompt must enforce one question per turn
- **Not matching toolCallId** — copy it exactly from the incoming request
- **Assuming caller has all booking info** — always check `is_bookable()` before committing
- **Calling Google APIs synchronously** inside async route handlers — blocks everything

---

## Useful Commands

```bash
# Start everything
docker run -d -p 6379:6379 redis:alpine
uvicorn api.main:app --reload --port 8000

# Seed calendar
python scripts/seed_calendar.py

# Expose to Vapi (Phase 2)
ngrok http 8000

# Create Vapi assistant (once)
python vapi/setup.py

# Watch Logfire traces
# Open https://logfire.pydantic.dev — traces appear automatically
```

---

## Key Files Quick Reference

| File | Purpose |
|---|---|
| `agent/core.py` | PydanticAI agent definition |
| `agent/session.py` | BookingSession + Redis SessionStore |
| `agent/tools/calendar.py` | check_slot, book_class, reschedule, cancel |
| `agent/tools/sheets.py` | log_call, get_caller_history |
| `agent/tools/escalate.py` | escalate_to_human |
| `agent/prompts/system.py` | System prompt (voice-optimised) |
| `api/routes/chat.py` | Phase 1 text chat endpoint |
| `api/routes/vapi.py` | Phase 2 Vapi webhook handler |
| `integrations/google_calendar.py` | Async GCal wrapper |
| `integrations/google_sheets.py` | Async Sheets wrapper |
| `scripts/seed_calendar.py` | Seed test data |
| `vapi/setup.py` | Create Vapi assistant via API |
| `ui/index.html` | Minimal Phase 1 chat UI |
