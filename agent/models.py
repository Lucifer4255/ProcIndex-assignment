"""Agent data models.

BookingSession is the structured state the agent and tools use while helping a
caller book, reschedule, cancel, or escalate a request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class BookingSession:
    class_type: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    caller_name: str | None = None
    caller_phone: str | None = None
    call_id: str = ""
    caller_history: dict[str, Any] | None = None
    slot_confirmed: bool = False
    booking_confirmed: bool = False

    def missing_for_booking(self) -> list[str]:
        missing: list[str] = []
        if not self.class_type:
            missing.append("class type")
        if not self.preferred_date:
            missing.append("date")
        if not self.preferred_time:
            missing.append("time")
        if not self.caller_name:
            missing.append("name")
        if not self.caller_phone:
            missing.append("phone number")
        return missing

    def is_bookable(self) -> bool:
        return len(self.missing_for_booking()) == 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BookingSession:
        return cls(
            class_type=data.get("class_type"),
            preferred_date=data.get("preferred_date"),
            preferred_time=data.get("preferred_time"),
            caller_name=data.get("caller_name"),
            caller_phone=data.get("caller_phone"),
            call_id=data.get("call_id", ""),
            caller_history=data.get("caller_history"),
            slot_confirmed=bool(data.get("slot_confirmed", False)),
            booking_confirmed=bool(data.get("booking_confirmed", False)),
        )
