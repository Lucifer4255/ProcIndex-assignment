"""Create or update the Elliot Vapi assistant — run once to register the assistant.

Usage:
    uv run python vapi/setup.py

Prints the assistant ID. Copy it into VAPI_ASSISTANT_ID in .env so subsequent
runs update the existing assistant instead of creating a new one.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from vapi import AsyncVapi
from vapi.types import OpenAiFunction, OpenAiFunctionParameters, Server
from vapi.types.create_assistant_dto_model import CreateAssistantDtoModel_Openai
from vapi.types.create_assistant_dto_voice import CreateAssistantDtoVoice_Vapi
from vapi.types.open_ai_model_tools_item import OpenAiModelToolsItem_Function

load_dotenv()

_SYSTEM_PROMPT = """
IDENTITY
You are Elliot, the receptionist at Solstice Pilates in San Francisco. You help callers book classes, answer questions about the studio, and handle common requests warmly and efficiently.

PERSONALITY
Be warm, natural, and efficient. Use short sentences and a conversational tone. Avoid filler like "Certainly!", "Absolutely!", or "Great question!" Speak as Elliot. Skip bullet points and numbered lists. Once you know the caller's name, address them by it naturally.

RESPONSE RULES
Keep replies to at most two sentences and ask only one question per turn. No markdown. Before a tool call you may say a brief filler like "Let me check that for you." Only mention class times, dates, prices, or availability that a tool returned — never invent slots. If you tell the caller "I've logged" or "a manager will follow up", you MUST have called log_call or escalate_to_human.

STUDIO CONTEXT
Classes: Reformer drop-in thirty-five dollars, Mat drop-in twenty-five dollars, Tower drop-in forty dollars. Hours: Monday through Saturday six a.m. to eight p.m., Sunday eight a.m. to two p.m. Drop-ins are welcome when space is available.

WORKFLOW
When the caller uses a relative date phrase like "this Saturday", "next Monday", or "this week", call get_current_date first to resolve the exact date before calling check_slot.

For availability or scheduling, call check_slot right away with whatever the caller has given you. For a booking, collect class type, date, time, name, phone — but reuse name and phone from session context if already known. Call book_class once the caller confirms a slot and you have name and phone.

When the caller wants to reschedule or cancel, get their phone and call lookup_existing_bookings first, before asking anything else. Then use reschedule to move a booking or cancel_booking to remove one.

For group bookings, private sessions, or birthday parties, explain a manager will follow up, collect name and callback phone, then call log_call with reason group_booking, priority high, callback_required true.

For billing, refunds, injuries, instructor complaints, or membership cancellations: acknowledge calmly, collect name and callback number, then call escalate_to_human.
""".strip()


def _fn_tool(
    name: str,
    description: str,
    properties: dict,
    required: list[str] | None = None,
    server_url: str | None = None,
) -> OpenAiModelToolsItem_Function:
    params = OpenAiFunctionParameters(
        type="object",
        properties=properties,
        required=required or [],
    )
    fn = OpenAiFunction(name=name, description=description, parameters=params)
    server = Server(url=server_url) if server_url else None
    return OpenAiModelToolsItem_Function(function=fn, server=server)


def _build_tools(webhook_url: str) -> list[OpenAiModelToolsItem_Function]:
    url = webhook_url
    s = {"type": "string"}

    return [
        _fn_tool(
            "get_current_date",
            "Get today's date and timezone. Call this FIRST whenever the caller uses a relative date like 'this Saturday', 'next Monday', or 'this week' so you can resolve the exact YYYY-MM-DD date before checking availability.",
            {},
            server_url=url,
        ),
        _fn_tool(
            "check_slot",
            "Check class slot availability. Call immediately when the caller asks about schedule, availability, or wants to book — even with partial info.",
            {
                "class_type": {"type": "string", "enum": ["Reformer", "Mat", "Tower"], "description": "Class name. Omit if not specified."},
                "date_value": {**s, "description": "Date in YYYY-MM-DD. Omit if unknown."},
                "time_value": {**s, "description": "Time like '6pm', '18:00'. Omit if unknown."},
            },
            server_url=url,
        ),
        _fn_tool(
            "lookup_existing_bookings",
            "Fetch all upcoming bookings for a caller. Call this FIRST when they want to reschedule, cancel, or ask about their booking.",
            {"caller_phone": {**s, "description": "Caller's phone number. REQUIRED."}},
            required=["caller_phone"],
            server_url=url,
        ),
        _fn_tool(
            "book_class",
            "Complete a class booking. Call only after the caller confirmed a specific slot AND you have their name and phone.",
            {
                "class_type": {"type": "string", "enum": ["Reformer", "Mat", "Tower"]},
                "date_value": {**s, "description": "Date in YYYY-MM-DD."},
                "time_value": {**s, "description": "Time like '9am', '6pm'."},
                "caller_name": {**s, "description": "Caller's full name. REQUIRED."},
                "caller_phone": {**s, "description": "Caller's phone number. REQUIRED."},
            },
            required=["caller_name", "caller_phone"],
            server_url=url,
        ),
        _fn_tool(
            "reschedule",
            "Move an existing booking to a new date/time. Requires new_date and new_time from the caller. Do NOT call check_slot for reschedule.",
            {
                "new_date": {**s, "description": "Date to move TO in YYYY-MM-DD. REQUIRED."},
                "new_time": {**s, "description": "Time to move TO, e.g. '6pm'. REQUIRED."},
                "old_date": {**s, "description": "Date of existing booking. Omit to use session context."},
                "old_time": {**s, "description": "Time of existing booking. Omit to use session context."},
                "new_class_type": {**s, "description": "New class type if switching. Omit to keep same."},
                "caller_phone": {**s, "description": "Caller's phone. REQUIRED."},
                "caller_name": {**s, "description": "Caller's name, optional."},
            },
            required=["new_date", "new_time", "caller_phone"],
            server_url=url,
        ),
        _fn_tool(
            "cancel_booking",
            "Cancel an existing booking. Requires caller phone. Date/time optional — auto-finds if omitted.",
            {
                "caller_phone": {**s, "description": "Caller's phone. REQUIRED."},
                "date_value": {**s, "description": "Date of booking to cancel. Omit to auto-find."},
                "time_value": {**s, "description": "Time of booking to cancel. Omit to auto-find."},
                "caller_name": {**s, "description": "Caller's name, optional."},
            },
            required=["caller_phone"],
            server_url=url,
        ),
        _fn_tool(
            "get_caller_history",
            "Look up a returning caller's history in the Contacts sheet. Call early when you have their phone to greet them by name.",
            {"phone": {**s, "description": "Caller phone. Omit to use session phone."}},
            server_url=url,
        ),
        _fn_tool(
            "log_call",
            "Log this call or a follow-up request. Use for bookings, inquiries, group parties, waitlist, feedback. NOT for billing/refunds — use escalate_to_human instead.",
            {
                "reason": {"type": "string", "enum": ["booking", "reschedule", "cancel", "inquiry", "follow_up", "missed_callback", "group_booking", "membership_inquiry", "waitlist", "feedback", "lost_and_found", "late_arrival", "complaint"]},
                "summary": {**s, "description": "One or two sentence summary of the call."},
                "priority": {"type": "string", "enum": ["normal", "high", "urgent"], "description": "normal for handled calls, high for manager follow-up, urgent for missed callbacks."},
                "callback_required": {"type": "boolean", "description": "True when manager should call back."},
                "notes": {**s, "description": "Extra detail."},
                "phone": {**s, "description": "Caller phone if not in session."},
                "name": {**s, "description": "Caller name if not in session."},
            },
            required=["reason", "summary"],
            server_url=url,
        ),
        _fn_tool(
            "escalate_to_human",
            "Escalate billing, refund, injury, instructor complaint, membership cancellation, or abuse to a manager. Collect callback number first.",
            {
                "reason": {"type": "string", "enum": ["billing", "refund", "injury", "instructor_complaint", "membership_cancel", "abuse", "other"]},
                "callback_number": {**s, "description": "Best number for callback. Omit if in session."},
                "notes": {**s, "description": "What happened, when charged, injury description."},
                "name": {**s, "description": "Caller name if known."},
            },
            required=["reason"],
            server_url=url,
        ),
    ]


async def main() -> None:
    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        print("ERROR: VAPI_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    base_url = os.environ.get("BASE_URL", "").rstrip("/")
    if not base_url:
        print("ERROR: BASE_URL not set in .env", file=sys.stderr)
        sys.exit(1)

    llm_model = os.environ.get("VAPI_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct")
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID", "").strip()

    webhook_url = f"{base_url}/vapi/webhook"
    tools = _build_tools(webhook_url)

    model = CreateAssistantDtoModel_Openai(
        model=llm_model,
        messages=[],
        tools=tools,
        temperature=0.3,
    )

    voice = CreateAssistantDtoVoice_Vapi(voice_id="Elliot")

    client = AsyncVapi(token=api_key)
    kwargs = dict(
        name="Elliot — Solstice Pilates",
        first_message="Hi, this is Elliot at Solstice Pilates. How can I help you today?",
        model=model,
        voice=voice,
        server_messages=["tool-calls", "end-of-call-report"],
        server=Server(url=webhook_url, timeout_seconds=30),
    )

    if assistant_id:
        assistant = await client.assistants.update(assistant_id, **kwargs)
        print(f"Updated assistant: {assistant.id}")
    else:
        assistant = await client.assistants.create(**kwargs)
        print(f"Created assistant: {assistant.id}")
        print(f"\nAdd this to your .env:\n  VAPI_ASSISTANT_ID={assistant.id}")


if __name__ == "__main__":
    asyncio.run(main())
