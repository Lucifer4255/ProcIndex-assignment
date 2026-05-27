# Tradeoffs & Optimizations

A running list of design decisions we've made and ideas worth considering if this app grew beyond the take-home scope. Each item explains what we chose, why, and what would change the calculus.

---

## 1. Booking storage: plain text vs JSON vs Redis index

**Current:** Each Calendar event's `description` stores bookings as `Booked: Sara (+14155550190), Mike (+14155550191)`. A regex parses it back.

**Considered:** Storing a JSON map `{"+14155550190": "Sara", ...}` in the description, or maintaining a separate Redis index `phone → [event_id, ...]`.

**Why we kept the current format:**
- Human-readable in the Google Calendar UI when staff peek at an event
- Per-slot lookup is O(15) max — a hashmap doesn't measurably help
- The real cost in `find_booking_by_phone` is the Google Calendar API call across a 60-day range, not the in-memory parse

**When to revisit:**
- If call volume gets high enough that scanning 60 days of events per cancellation/reschedule shows up in latency → add a Redis `phone → event_id` index, updated on every booking write
- If we need to store more per-booking metadata (booking timestamp, source, no-show flag, notes) → switch to JSON in the description

---

## 2. Model fallback chain ordering

**Current:** `FallbackModel(gemini-2.5-flash-lite, llama-3.3-70b-instruct, claude-haiku-4-5)`.

**Tradeoff:** Cheapest/fastest first, most reliable tool-caller last. We pay for the cheap model's failures (one wasted call before fallback fires) in exchange for near-zero cost on the happy path.

**When to revisit:**
- If we see frequent fallbacks in Logfire (>5% of turns), reorder — the "cheap" model is actually expensive once you count the wasted call + the fallback
- For Phase 2 voice, latency matters more than cost; consider putting the most consistent tool-caller first to avoid the cold-start of falling through

---

## 3. Tool design: pass-all-fields vs setter tools

**Current:** `book_class(class_type, date, time, name, phone)` accepts every field as an argument and merges into `BookingSession`. There is no separate `set_caller_name` tool.

**Why:**
- Fewer tools = clearer model decisions (Anthropic guidance: keep active set small)
- Bug we hit earlier: the model collected name/phone in text but never persisted them, because there was no tool that wrote them. Making `book_class` accept them forces the model to pass them at call time.

**Alternative:** A dedicated `update_session(field, value)` tool. Rejected — more tools means more chances the model picks the wrong one, and the model already has to know the values to call any tool.

---

## 4. Timezone handling: studio-local everywhere

**Current:** All seed times, all `format_time_label` output, all `studio_datetime` constructions use `America/Los_Angeles`. The runtime-context instruction tells the model "today is X in PT."

**Tradeoff:** Simple, but breaks if Solstice ever opens a second studio in a different timezone. Right now there's only one studio, so the cost of multi-tz handling isn't worth paying.

**When to revisit:** Multi-location support — switch to storing UTC + a `studio_id → tz` lookup.

---

## 5. Session storage TTL

**Current:** Redis `session:{id}` with TTL 3600s (1 hour).

**Tradeoff:** A caller who comes back after lunch loses their session and starts fresh. Acceptable for voice (calls end and don't resume); for chat, a returning user gets a clean slate.

**When to revisit:**
- If we want returning callers to resume mid-booking → bump TTL to 24h
- If we add a "remember me" pattern keyed off phone number → look up by phone in Sheets instead of relying on session_id

---

## 6. Recurring slot generation

**Current:** Seed script writes 2 weeks of class slots up front. If a caller asks for a date beyond the seeded range, `check_slot` returns nothing.

**Considered (Step 3.5 in execution.md):** Generate slots on-demand from a recurring schedule rule set.

**Tradeoff:** Cleaner UX, but requires rule-validation logic (don't let the agent invent Sunday-night classes). Deferred until Phase 1 happy path is solid.

---

## 7. Tool-call retry & loop prevention

**Current:** `UsageLimits(tool_calls_limit=10)` caps total tool calls per turn. We saw a real loop earlier (the model called `book_class` 4× in a row before giving up) — without this limit it could have gone much further.

**Could add:** Per-tool retry counts via `@agent.tool(retries=2)` to give friendlier feedback on the second failure, or `ModelRetry` exceptions from inside tools to nudge the model toward correction instead of generic apologies.

---

## 8. Phone number validation

**Current:** `normalize_phone` accepts 10-digit US, 11-digit US with leading 1, or passes through unchanged. No format validation — `+1456663538` (only 9 digits) just gets stored as-is.

**Tradeoff:** Permissive — won't reject a caller who flubs a digit. But staff might call back a bad number.

**Could add:** Length + country-code validation in `book_class` returning a friendly retry to the model, so it asks the caller to repeat.

---

## 9. No traditional database

**Current:** Three storage layers — Google Calendar (bookings), Google Sheets (caller history / call log), Redis (in-flight session state). No Postgres / MySQL / Mongo.

**Why no DB:**
- Google Calendar and Sheets are the systems studio staff already use. Writing through them means staff can see, edit, and audit data without a separate admin UI.
- Calendar is already a domain-aware booking store: events have start/end, attendees, descriptions. Reinventing it in a relational schema duplicates capability for no gain.
- Bookings need to be visible to non-technical staff in real time. A DB would force us to also build a staff-facing UI; Calendar gives that for free.
- For a single-studio take-home (and even a multi-studio MVP), the GCal API is fast enough. Rate limits sit at thousands of requests/day per project — well above realistic call volume.
- Redis covers the one thing GCal/Sheets are bad at: low-latency ephemeral state during a multi-turn conversation.

**When to revisit:**
- Multi-studio scale where GCal rate limits start biting → introduce Postgres as a write-through cache, with GCal as a synced view for staff
- Reporting/analytics needs that aren't ergonomic in Sheets (joins, aggregations, dashboards) → ship events to a warehouse (BigQuery, Snowflake) rather than adding an OLTP DB
- Compliance requirements demanding audit logs, encryption-at-rest guarantees, or PII handling Google can't satisfy

**What we'd lose if we added a DB now:**
- Direct staff visibility into bookings without building a UI
- Sync complexity (DB ↔ Calendar drift, race conditions, dual-write failures)
- One more piece of infra to provision, back up, and monitor for a single-studio app

---

## 10. Two-tab Sheet split: aggregate + append-only log

**Current:** `Contacts` tab is one row per caller (per phone) — name, call_count, last_called, last_class_booked, last_call_*, priority_flag, callback_required. Every successful booking/reschedule/cancel/log_call/escalate also appends to a separate `CallLog` tab — one row per event with timestamp, phone, reason, summary, priority, callback_required, notes.

**Why two tabs:** A single aggregate row loses event history. If Alan escalates a billing issue (priority=urgent, callback_required=TRUE), then later books a class, the booking's reason overwrites "billing" in last_call_reason — the manager dashboard can no longer tell *what* the urgent issue is. Splitting fixes this: Contacts shows the current state, CallLog shows the full thread.

**Manager workflow:**
- Filter `Contacts` by `priority_flag=urgent` → list of callers needing attention
- For each caller, sort `CallLog` by phone + timestamp → see the full conversation history
- Resolving the urgent issue means manually clearing the Contacts flag; the CallLog row stays

**Tradeoff:** Two writes per logging tool call → 2× Sheets API quota usage. CallLog grows unbounded (could need archival after a year of high call volume). Acceptable for any realistic studio scale.

**Why not a DB:** Same reasoning as item #9 — staff want to read and edit this in Sheets. A separate logs table in Postgres would lose that affordance.

---

## 11. Sheets header-row detection is permissive by convention, not by code

**Current:** `GoogleSheetsClient.get_row_by_phone` only recognises a header row when `rows[0][0].lower() == "phone"`. Any other label ("Phone Number", "Caller Phone", blank A1, etc.) causes the header row to be treated as data.

**Tradeoff:** Keeps the code simple and assumes whoever provisions the sheet uses the documented column name. A more defensive parser would detect headers by row-number heuristics or column-shape inspection, but that adds complexity for a one-time setup contract.

**When to revisit:** if anyone other than the founding team starts provisioning sheets, switch to a small `COLUMNS`-aware header inspector or move to a typed schema (e.g. gspread + pydantic row model).

---

## 11. Description as source of truth

**Current:** Booking data lives in the Calendar event description; Redis only holds in-flight session state. If Redis dies, no bookings are lost.

**Tradeoff:** Every booking mutation is a Calendar API round-trip (slow, rate-limited). The trade is worth it for durability — Calendar is the authoritative system staff already check.

**Could add:** Write-through cache in Redis keyed by event_id, with TTL short enough that stale data doesn't matter. Optimizes read-heavy reschedule/cancel flows.
