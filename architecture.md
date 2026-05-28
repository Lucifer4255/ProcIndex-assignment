# Solstice Pilates — AI Receptionist Architecture

> **Interactive diagram:** [View on Miro](https://miro.com/app/board/uXjVIlldD90=/?moveToWidget=3458764673515089855) · [Architecture walkthrough (Loom)](https://www.loom.com/share/9a933e49e3174a98a0f218605014f405)

## Overview

An AI voice and text receptionist for Solstice Pilates (San Francisco). Handles ~30 inbound calls/day. Phase 1 is a text chat interface. Phase 2 wires the same domain tools into Vapi for live voice calls.

The agent uses progressive elicitation — it never fails on missing booking info, it asks for each piece naturally across turns until it has everything it needs to act.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12+ | PydanticAI RunContext is the cleanest solution for elicitation state |
| Agent framework | PydanticAI v1.x | Dependency injection via RunContext handles BookingSession elegantly |
| Web server | FastAPI + uvicorn | Async, native Pydantic, fast webhook responses |
| Session store | Redis | Fast reads per webhook call, TTL-based cleanup, keyed by call.id |
| LLM | Configurable via env — see Model Options below | Swappable across providers; OpenRouter recommended for dev |
| Observability | Logfire | First-party PydanticAI integration, one decorator |
| Calendar | Google Calendar API v3 (google-api-python-client) | freebusy query + events insert/patch/delete |
| Caller log | Google Sheets API v4 | Contacts tab, one row per caller |
| Voice platform | Vapi | Phase 2 — handles STT, LLM routing, TTS. We own only the tool webhook |
| Tunneling (dev) | ngrok | Expose local FastAPI to Vapi during development |

---

## System Layers

Phase 1 (text chat) and Phase 2 (voice) share the same domain tools but use different runtimes. Vapi runs its own LLM — our server only handles tool-call webhooks.

```
Phase 1 — Text Chat               Phase 2 — Voice
────────────────────               ────────────────────────────────
Chat UI                            Vapi  (STT → Vapi LLM → TTS)
  │ POST /chat                       │ tool-calls webhook (HTTP)
  ▼                                  ▼
PydanticAI Agent (Elliot)          POST /vapi/webhook
BookingSession in RunContext        VapiService — dispatches tool calls
message_history in Redis            BookingSession in Redis (per call.id)
  │ tool call / result               │ tool call / result
  └──────────────┬───────────────────┘
                 ▼
    Domain Tools  (tools/)
    ┌──────────────────────────────────────────┐
    │ check_slot          lookup_existing_bookings │
    │ book_class          get_current_date         │
    │ reschedule          log_call                 │
    │ cancel_booking      get_caller_history        │
    │                     escalate_to_human         │
    └──────────────────────────────────────────┘
         │                          │
  Google Calendar              Google Sheets
  events · capacity            Contacts tab
```

---

## File Structure

Ports-and-adapters layout: pure domain in `tools/`, PydanticAI shims in `agent/tools/`, Vapi adapter standalone in `vapi_adapter/`.

```
solstice-receptionist/
├── tools/                       # Domain layer — no framework imports
│   ├── clock.py                 # get_current_date (PST)
│   ├── calendar.py              # check_slot, book_class, reschedule, cancel_booking,
│   │                            #   lookup_existing_bookings
│   ├── sheets.py                # log_call, get_caller_history
│   └── escalate.py              # escalate_to_human
├── agent/                       # Phase 1 — PydanticAI adapter
│   ├── core.py                  # Agent definition, model config
│   ├── models.py                # BookingSession dataclass
│   ├── storage.py               # Redis SessionStore
│   ├── tools/                   # Thin @agent.tool shims wrapping tools/
│   │   ├── calendar.py
│   │   ├── sheets.py
│   │   └── escalate.py
│   └── prompts/
│       └── system.py            # Chat system prompt (6-section format)
├── vapi_adapter/                # Phase 2 — Vapi HTTP adapter
│   ├── service.py               # VapiService: load session, dispatch tool, save session
│   └── router.py                # FastAPI router — POST /vapi/webhook
├── api/
│   ├── main.py                  # FastAPI app, lifespan, CORS, Logfire
│   ├── routes/
│   │   └── chat.py              # POST /chat — Phase 1
│   └── settings.py              # Pydantic settings
├── integrations/
│   ├── google_calendar.py       # GCal service wrapper (sync → async via executor)
│   └── google_sheets.py         # Sheets service wrapper
├── scripts/
│   ├── vapi_setup.py            # Create/update Vapi assistant (run manually)
│   └── seed_calendar.py         # Seed 2 weeks of class slots + sample bookings
├── ui/
│   └── index.html               # Chat UI (plain HTML/JS, no framework)
├── .env.example
├── pyproject.toml
├── architecture.md
└── README.md
```

---

## Agent Core

### BookingSession (RunContext deps)

```python
from dataclasses import dataclass, field

@dataclass
class BookingSession:
    # Elicitation state — filled progressively across turns
    class_type: str | None = None        # "Reformer" | "Mat" | "Tower"
    preferred_date: str | None = None    # ISO date "2024-01-18"
    preferred_time: str | None = None    # "18:00"
    caller_name: str | None = None
    caller_phone: str | None = None

    # Call metadata
    call_id: str = ""
    caller_history: dict | None = None   # loaded from Sheets on call start

    # Booking flow control
    slot_confirmed: bool = False
    booking_confirmed: bool = False

    def missing_for_booking(self) -> list[str]:
        missing = []
        if not self.class_type:      missing.append("class type")
        if not self.preferred_date:  missing.append("date")
        if not self.preferred_time:  missing.append("time")
        if not self.caller_name:     missing.append("name")
        if not self.caller_phone:    missing.append("phone number")
        return missing

    def is_bookable(self) -> bool:
        return len(self.missing_for_booking()) == 0
```

### Session store (Redis)

```python
# Key pattern: "session:{call_id}"
# Stores: BookingSession (as JSON) + message_history (as JSON list)
# TTL: 3600 seconds (1 hour)

# agent/storage.py
class SessionStore:
    async def get(self, call_id: str) -> tuple[BookingSession, list] | None
    async def save(self, call_id: str, session: BookingSession, history: list) -> None
    async def delete(self, call_id: str) -> None
```

### Message history (cross-turn context)

```python
# After each agent.run():
result = await agent.run(user_input, deps=session, message_history=history)
history = result.all_messages()           # capture full history
await store.save(call_id, session, history)   # persist for next turn
```

---

## Tool Definitions

Domain tools live in `tools/` as plain async functions. `agent/tools/` wraps each with `@agent.tool` for Phase 1. `vapi_adapter/service.py` dispatches to the same domain functions directly for Phase 2.

### get_current_date
```
Action: Returns current date, time, and timezone (America/Los_Angeles).
Output: Human-readable string: "Today is Wednesday, May 28, 2025 (2025-05-28), 11:45 AM PST."
Note:   Called first whenever the caller uses a relative phrase like "this Saturday".
```

### check_slot
```
Input:  class_type (optional), date_value (optional), time_value (optional)
Action: Reads GCal events for the requested window. Filters past slots.
Output: Available slots with spot counts. Marks slots matching existing_bookings
        as "(your current booking)" when lookup_existing_bookings was called first.
```

### lookup_existing_bookings
```
Input:  caller_phone
Action: Scans next 60 days of GCal for events booked under that phone.
        Stores results in BookingSession.existing_bookings.
Output: Summary of found bookings, e.g. "Found 1 booking: Mat 9am on Friday May 29."
Note:   Call first in any reschedule or cancel flow so check_slot can annotate results.
```

### book_class
```
Input:  class_type, date_value, time_value, caller_name, caller_phone
Action: Patches GCal class-slot event to append attendee. Stores confirmation in session.
Output: Booking confirmation string.
```

### reschedule
```
Input:  new_date, new_time, caller_phone; old_date/old_time optional
Action: Resolves old slot from args → existing_bookings → session context.
        Removes attendee from old slot, adds to new slot.
Output: Confirmation with old and new slot details.
```

### cancel_booking
```
Input:  caller_phone; date_value/time_value optional
Action: Resolves slot from args → existing_bookings → session context.
        Removes attendee from GCal event.
Output: Cancellation confirmation.
```

### get_caller_history
```
Input:  phone (falls back to session phone)
Action: Reads row from Sheets Contacts tab.
Output: name, last_class_booked, last_called, call_count — or "first-time caller".
```

### log_call
```
Input:  reason, summary, priority ("normal"|"high"|"urgent"), callback_required (bool)
Action: Upserts row in Sheets Contacts tab keyed by phone.
Output: Confirmation string.
```

### escalate_to_human
```
Input:  reason, callback_number (optional), name (optional), notes (optional)
Action: Logs escalation to Sheets with priority flag. Returns closing script for agent.
Note:   Vapi then calls transferCall to connect caller to manager line.
```

---

## Google Calendar Integration

### Class event structure
```
Summary:  "Reformer Class — 6pm"
Start:    2024-01-18T18:00:00-08:00
End:      2024-01-18T19:00:00-08:00
Description: "CLASS_SLOT | Type: Reformer | Capacity: 10 | Booked: Sara (+14155550190), ..."
Location: "Solstice Pilates, San Francisco"
```

### Availability check (freebusy)
```python
# Use Calendar events as the source of scheduled class slots.
# Google Calendar does not know class capacity; our app stores capacity/bookings
# in class slot event metadata/description and computes remaining spots itself.
# freebusy can help identify time conflicts, but it is not the capacity model.
```

### Booking (event insert)
```python
# Patch the existing class slot event to append the attendee in the booking list
# Use asyncio.get_event_loop().run_in_executor(None, sync_fn) for all GCal calls
# GCal client is synchronous — must be wrapped for async FastAPI
```

### Seed data (scripts/seed_calendar.py)
- 2 weeks of recurring class events: Mon–Sat, 6am / 9am / 6pm / 7pm
- Class types: Reformer (capacity 10), Mat (capacity 15), Tower (capacity 8)
- 3 pre-existing bookings to test conflicts and reschedule flows
- One fully booked class to test the "slot full → offer alternative" path

---

## Google Sheets Schema

**Sheet name:** Solstice Pilates  
**Tab name:** Contacts

| Column | Type | Notes |
|---|---|---|
| phone | string | Primary lookup key. E.164 format. |
| name | string | Filled on first booking. |
| last_called | ISO timestamp | Updated every call. |
| call_count | integer | Incremented every call. |
| last_class_booked | string | e.g. "Reformer 6pm Thu" |
| last_call_reason | string | booking / reschedule / cancel / inquiry / complaint / etc |
| last_call_summary | string | 1-2 sentence summary of the call |
| priority_flag | string | normal / high / urgent |
| callback_required | boolean | Set to TRUE by escalate tool |
| notes | string | Free text. Injury reports, complaints, etc. |

---

## FastAPI Routes

### POST /chat (Phase 1)

```
Request:  { "message": string, "session_id": string }
Flow:     Load session from Redis → run agent → save session → return response
Response: { "response": string, "session_id": string }
```

### POST /vapi/webhook (Phase 2)

```
Handles two event types from Vapi:

1. message.type == "tool-calls"
   - Extract call.id (session key) and caller phone from call.customer.number
   - Load session from Redis
   - Run the named tool with provided arguments
   - Save updated session
   - Return: { "results": [{ "toolCallId": "...", "result": "..." }] }
   - Always HTTP 200. Never line breaks in result string.

2. message.type == "end-of-call-report"
   - Extract transcript and summary from payload
   - Call log_call tool with summary
   - Clean up Redis session
   - Return: HTTP 200
```

**Critical Vapi response rules:**
- Always return HTTP 200, even for errors
- `result` must be a plain string — no objects, no arrays
- No newline characters in result string
- `toolCallId` must exactly match the ID from the request

---

## System Prompt Structure

Two separate prompts — same 6-section skeleton, tuned differently:

| | `agent/prompts/system.py` (Phase 1 chat) | `scripts/vapi_setup.py` `_SYSTEM_PROMPT` (Phase 2 voice) |
|---|---|---|
| Identity | Elliot, Solstice Pilates receptionist | Same |
| Personality | Warm, short sentences, no filler phrases | Same, slightly terser for voice |
| Response rules | ≤2 sentences, one question per turn, no markdown | Same + spell out phone digits |
| Studio context | Prices, hours, drop-in policy | Same |
| Workflow | Group/birthday → name+phone only → log_call; billing/injury → escalate; regular booking → check_slot first | Same, with explicit tool parameter values (e.g. `priority="high"`) for reliability |
| Few-shot examples | Full booking, reschedule, cancel examples | Same |

**Key voice-specific rules in `_SYSTEM_PROMPT`:**
- Group/birthday: collect name and phone only — `log_call` with `reason="group_booking"`, `priority="high"`, `callback_required=true`
- All tool parameter values written as quoted strings to prevent enum mismatches
- `get_current_date` called before any relative-date slot check

---

## Decision Matrix Summary

| Category | Scenarios | Agent behaviour |
|---|---|---|
| Fully handled | Availability, booking, reschedule, cancel, pricing, hours, running late, drop-in, class info, first-timer FAQ, returning caller | Completes end-to-end. Logs to Sheets on booking actions. |
| Partial | Birthday/group, membership inquiry, waitlist, feedback, lost and found | Answers what it can. Collects contact. Logs for human follow-up. |
| Escalate | Billing complaint, refund, instructor complaint, injury report, membership cancel, abusive caller | Acknowledges only. Collects callback number. Logs with priority flag. |

---

## Model Options

Model is configurable via env vars — no hardcoded provider. Choose based on phase and budget.

| Phase | Recommended | Why |
|---|---|---|
| Phase 1 dev | `meta-llama/llama-3.3-70b-instruct` via OpenRouter | Budget-friendly; validate tool calling before relying on it |
| Phase 1 final check | `anthropic/claude-sonnet-4-5` via OpenRouter | Best instruction following if llama is flaky |
| Phase 2 voice dev | `meta-llama/llama-3.3-70b-instruct` via Vapi OpenRouter provider | Low setup overhead with Vapi native OpenRouter support |
| Phase 2 final demo | `anthropic/claude-sonnet-4-5` via OpenRouter or direct Anthropic | Best quality for submission video |

**Key rule:** if tool calling is unreliable on a cheaper model, bump up — don't fight the prompt.

### PydanticAI model config

```python
# agent/core.py — reads from env
import os
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

model = OpenRouterModel(
    os.environ["LLM_MODEL"],
    provider=OpenRouterProvider(api_key=os.environ["LLM_API_KEY"]),
)

agent = Agent(model=model, deps_type=BookingSession, ...)
```

PydanticAI has native OpenRouter support. Model IDs should be OpenRouter model IDs like `meta-llama/llama-3.3-70b-instruct` or `anthropic/claude-sonnet-4-5`.

---

## Vapi Assistant Config (Phase 2)

Managed by `scripts/vapi_setup.py` — run manually to create or update the assistant. Tool descriptions are read from agent tool docstrings (`_doc(fn)`) so there is one source of truth.

```python
# scripts/vapi_setup.py (simplified)
model = CreateAssistantDtoModel_Openai(
    model=os.environ.get("VAPI_LLM_MODEL", "gpt-4o"),
    tools=_build_tools(webhook_url),   # each tool → POST /vapi/webhook
    temperature=0.3,
)
voice = CreateAssistantDtoVoice_Vapi(voice_id="Elliot")

# Tool descriptions sourced from agent docstrings:
_doc(_agent_cal.check_slot)   # → Vapi tool description
```

**Tool webhook flow:**
1. Vapi LLM decides to call a tool → sends `POST /vapi/webhook` with `type=tool-calls`
2. `VapiService.handle_tool_calls` loads `BookingSession` from Redis by `call.id`
3. Dispatches to the matching domain function in `_TOOL_DISPATCH`
4. Saves updated session, returns `{"results": [{"toolCallId": "...", "result": "..."}]}`
5. On `end-of-call-report`, session is deleted from Redis

**Critical Vapi response rules:**
- Always HTTP 200, even on errors
- `result` must be a plain string — no objects, no arrays, no newlines
- `toolCallId` must exactly match the ID from the request

**Latency:**
- All GCal calls wrapped in `run_in_executor` — never block the event loop
- Target < 2s per tool webhook response

---

## Environment Variables

```env
# Phase 1 LLM (PydanticAI agent)
LLM_API_KEY=                   # OpenRouter key
LLM_MODEL=anthropic/claude-sonnet-4-5  # or meta-llama/llama-3.3-70b-instruct for dev

# Phase 2 LLM (Vapi runs this — OpenAI model ID)
VAPI_LLM_MODEL=gpt-4o

# Google
GOOGLE_SERVICE_ACCOUNT_JSON=   # path to service account key file
GOOGLE_CALENDAR_ID=            # studio calendar ID
GOOGLE_SHEET_ID=               # sheet ID from URL

# Redis
REDIS_URL=redis://localhost:6379

# Vapi
VAPI_API_KEY=
VAPI_ASSISTANT_ID=             # set after first run of scripts/vapi_setup.py
VAPI_PHONE_NUMBER_ID=          # set after Twilio number is imported into Vapi

# Logfire
LOGFIRE_TOKEN=

# Server
PORT=8000
BASE_URL=                      # ngrok URL during dev, real URL in prod
```

---

## Development Setup

```bash
# 1. Install dependencies
uv sync  # or pip install -e .

# 2. Start Redis
docker run -d -p 6379:6379 redis:alpine

# 3. Start FastAPI
uvicorn api.main:app --reload --port 8000

# 4. Expose via ngrok (Phase 2 dev)
ngrok http 8000

# 5. Seed calendar
python scripts/seed_calendar.py

# 6. Create/update Vapi assistant (Phase 2)
uv run python scripts/vapi_setup.py
```

---

## Build Order

### Phase 1
1. Google Calendar integration + seed script
2. Google Sheets integration
3. `BookingSession` dataclass + Redis `SessionStore`
4. PydanticAI agent with all tools wired
5. FastAPI `/chat` route
6. Plain HTML chat UI

### Phase 2
1. Vapi assistant setup script
2. `/vapi/webhook` route handler
3. Voice system prompt tuning
4. Latency testing with ngrok + Vapi dashboard web call
5. Final demo call (Jio international → Vapi US number)
