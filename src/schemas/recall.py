from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, field_validator


class RecallRequest(BaseModel):
    query: str
    session_id: str
    user_id: Optional[str] = None
    max_tokens: int = 1024

    @field_validator("max_tokens")
    @classmethod
    def clamp_tokens(cls, v: int) -> int:
        return max(64, min(v, 32768))

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty")
        return v


class Citation(BaseModel):
    turn_id: str
    score: float
    snippet: str


class RecallResponse(BaseModel):
    context: str
    citations: list[Citation]
