from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator


class SearchRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    limit: int = 10

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, v: int) -> int:
        return max(1, min(v, 100))

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty")
        return v


class SearchResult(BaseModel):
    content: str
    score: float
    session_id: Optional[str]
    timestamp: Optional[str]
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    results: list[SearchResult]
