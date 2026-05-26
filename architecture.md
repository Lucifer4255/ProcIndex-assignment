# Solstice Pilates — AI Receptionist Architecture

## Overview

An AI voice and text receptionist for Solstice Pilates (San Francisco). Handles ~30 inbound calls/day. Phase 1 is a text chat interface. Phase 2 wires the same agent into Vapi for live voice calls.

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

```
┌─────────────────────────────────────────────────┐
│  Input channels                                  │
│  Chat UI (Phase 1)        Vapi (Phase 2)         │
│  POST /chat               STT → LLM → TTS        │
└────────────┬──────────────────┬──────────────────┘
             │                  │
┌────────────▼──────────────────▼──────────────────┐
│  FastAPI server                                   │
│  POST /chat     POST /vapi/webhook                │
│  (Phase 1)      tool-call · end-of-call           │
└────────────────────────┬──────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────┐
│  PydanticAI Agent                                  │
│  BookingSession (RunContext deps)                  │
│  message_history (loaded from Redis per turn)      │
│                         ↕                         │
│  Redis — session keyed by call.id / chat_id        │
└───┬──────────┬──────────┬──────────┬──────────────┘
    │          │          │          │
check_slot  book/     log_call  escalate
            reschedule
    │          │          │          │
    └────┬─────┘          └────┬─────┘
         │                     │
  Google Calendar        Google Sheets
  events · freebusy      Contacts tab
```

---

## File Structure

```
solstice-receptionist/
├── agent/
│   ├── __init__.py
│   ├── core.py              # PydanticAI agent definition, model config
│   ├── session.py           # BookingSession dataclass, SessionStore (Redis)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── calendar.py      # check_slot, book_class, reschedule, cancel
│   │   ├── sheets.py        # log_call, get_caller_history
│   │   └── escalate.py      # escalate_to_human
│   └── prompts/
│       └── system.py        # System prompt (6-section Vapi format)
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan, CORS, Logfire
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── chat.py          # POST /chat — Phase 1
│   │   └── vapi.py          # POST /vapi/webhook — Phase 2
│   └── schemas.py           # Pydantic request/response models
├── integrations/
│   ├── __init__.py
│   ├── google_calendar.py   # GCal service wrapper (sync → async via executor)
│   └── google_sheets.py     # Sheets service wrapper
├── vapi/
│   └── setup.py             # Script: create/update Vapi assistant via REST API
├── scripts/
│   └── seed_calendar.py     # Seed 2 weeks of class events + 3 existing bookings
├── ui/
│   └── index.html           # Minimal chat UI (plain HTML/JS, no framework)
├── .env.example
├── pyproject.toml
├── architecture.md
├── context.md
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

All tools are `@agent.tool` decorated async functions receiving `RunContext[BookingSession]`.

### check_slot
```
Input:  class_type (optional), date (optional), time (optional)
Action: Merges params into session. Calls GCal freebusy.
Output: Available slots with spot counts, or alternatives if full.
Note:   Never fails on missing params — returns what it found.
```

### book_class
```
Input:  (reads from ctx.deps — BookingSession)
Action: Checks session is_bookable(). If not, returns missing fields list.
        If bookable, creates GCal event with attendee note (phone).
        Sets session.booking_confirmed = True.
Output: Confirmation string or "still need: [fields]"
```

### reschedule
```
Input:  new_date, new_time (merges into session)
Action: Looks up existing event by caller_phone in GCal description.
        Updates event datetime.
Output: Confirmation with new slot details.
```

### cancel_booking
```
Input:  date (optional), time (optional)
Action: Finds event by caller_phone. Deletes it.
Output: Confirmation of cancellation.
```

### get_caller_history
```
Input:  phone (from session or Vapi payload)
Action: Reads row from Sheets Contacts tab where phone matches.
Output: Dict with name, last_class_booked, last_called, call_count.
        Returns None if first-time caller.
```

### log_call
```
Input:  reason, summary, priority ("normal"|"high"|"urgent")
Action: Upserts row in Sheets Contacts tab (keyed by phone).
        Updates: last_called, call_count, last_call_reason, last_call_summary.
Output: Confirmation string.
Note:   Always called at end-of-call via Vapi end-of-call-report webhook.
```

### escalate_to_human
```
Input:  reason, callback_number (optional), priority ("high"|"urgent")
Action: Calls log_call with escalation tag. Sets priority flag in Sheets.
Output: Script for agent: "I'll make sure [manager name] calls you back..."
Note:   Agent never commits to any outcome before calling this.
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

## System Prompt Structure (6-section Vapi format)

```
1. IDENTITY
   You are Maya, the receptionist at Solstice Pilates in San Francisco...

2. PERSONALITY  
   Warm, efficient, never robotic. Short sentences. Never say "Certainly!", 
   "Absolutely!", "Great question!", or "I'd be happy to help."
   Never use bullet points or lists. One question per response maximum.

3. RESPONSE RULES (voice-specific)
   - Max 2 sentences per response
   - Spell out all numbers: "four one five" not "415"
   - No markdown formatting
   - Use natural fillers while tools run: "Let me check that..."
   - If you need info, ask for ONE thing at a time

4. STUDIO CONTEXT (static knowledge)
   Classes: Reformer ($35 drop-in), Mat ($25 drop-in), Tower ($40 drop-in)
   Hours: Monday–Saturday 6am–8pm, Sunday 8am–2pm
   Drop-ins: welcome, subject to availability
   Private/group sessions: available for 6+ people, manager to confirm details

5. WORKFLOW
   Booking flow → check slot → confirm → get name + phone → complete
   Running late → acknowledge → note on calendar → end warmly
   Complaint/billing → acknowledge → collect callback → log urgent
   Returning caller → greet by name → reference last visit

6. FEW-SHOT EXAMPLES
   [Full conversation examples from assignment brief + edge cases]
```

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

```python
# vapi/setup.py — run once to create assistant
# Provider and model read from env

import os

VAPI_PROVIDER = os.environ.get("VAPI_LLM_PROVIDER", "openrouter")
VAPI_MODEL    = os.environ.get("VAPI_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct")

assistant = {
    "name": "Solstice Pilates Receptionist",
    "model": {
        "provider": VAPI_PROVIDER,       # "openrouter" | "anthropic" | "openai" etc.
        "model": VAPI_MODEL,
        "systemPrompt": SYSTEM_PROMPT,
        "temperature": 0.4,
    },
    "voice": {
        "provider": "11labs",
        "voiceId": "21m00Tcm4TlvDq8ikWAM",  # Rachel — warm, professional
    },
    "transcriber": {
        "provider": "deepgram",
        "model": "nova-2",
        "language": "en-US",
    },
    "firstMessage": "Thanks for calling Solstice Pilates, how can I help?",
    "endCallMessage": "Thanks for calling, have a great day!",
    "tools": [
        # Each tool points to POST /vapi/webhook with tool name in body
        # Vapi sends tool name + args, we route internally
    ]
}
```

**Latency tuning:**
- Keep tool webhook responses under 2s (GCal calls are the bottleneck)
- Wrap all GCal calls in `run_in_executor` — never block the event loop
- If voice latency feels slow, try Groq provider directly on Vapi (`provider: "groq"`, `model: "llama-3.3-70b-versatile"`)

---

## Environment Variables

```env
# LLM (OpenRouter recommended — OpenAI-compatible)
LLM_API_KEY=                   # OpenRouter key (or Anthropic key if using direct)
LLM_MODEL=meta-llama/llama-3.3-70b-instruct # swap to anthropic/claude-sonnet-4-5 for demo

# Vapi LLM (can differ from Phase 1 model)
VAPI_LLM_PROVIDER=openrouter
VAPI_LLM_MODEL=meta-llama/llama-3.3-70b-instruct

# Google
GOOGLE_SERVICE_ACCOUNT_JSON=   # path to service account key file
GOOGLE_CALENDAR_ID=            # studio calendar ID
GOOGLE_SHEET_ID=               # sheet ID from URL

# Redis
REDIS_URL=redis://localhost:6379

# Vapi
VAPI_API_KEY=
VAPI_PHONE_NUMBER_ID=          # created via Vapi dashboard or setup.py

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

# 6. Create Vapi assistant (Phase 2)
python vapi/setup.py
```

---

## Build Order

### Phase 1
1. Google Calendar integration + seed script
2. Google Sheets integration
3. `BookingSession` dataclass + `SessionStore`
4. PydanticAI agent with all tools wired
5. FastAPI `/chat` route
6. Plain HTML chat UI

### Phase 2
1. Vapi assistant setup script
2. `/vapi/webhook` route handler
3. Voice system prompt tuning
4. Latency testing with ngrok + Vapi dashboard web call
5. Final demo call (Jio international → Vapi US number)
