"""Pydantic request/response models."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = ""
    session_id: str = Field(min_length=1)


class ChatResponse(BaseModel):
    response: str
    session_id: str
