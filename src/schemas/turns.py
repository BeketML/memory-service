from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator


class Message(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class TurnRequest(BaseModel):
    session_id: str
    user_id: Optional[str] = None
    messages: list[Message]
    timestamp: datetime
    metadata: dict[str, Any] = {}

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("messages must not be empty")
        return v

    @field_validator("session_id")
    @classmethod
    def session_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v


class TurnResponse(BaseModel):
    id: str
