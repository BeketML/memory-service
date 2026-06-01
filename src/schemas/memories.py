from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class MemoryItem(BaseModel):
    id: str
    type: str
    key: str
    value: str
    confidence: float
    source_session: Optional[str]
    source_turn: Optional[str]
    created_at: datetime
    updated_at: datetime
    supersedes: Optional[str]
    superseded_by: Optional[str]
    active: bool
    stance: Optional[str] = None
    canonical_text: str


class MemoriesResponse(BaseModel):
    memories: list[MemoryItem]
