from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from src.schemas.memories import MemoriesResponse, MemoryItem
from src.storage.postgres.pool import get_pool
from src.storage.postgres.repos import memories as mem_repo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/users/{user_id}/memories")
async def get_memories(user_id: str) -> MemoriesResponse:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await mem_repo.get_all_memories(conn, user_id)

        items = []
        for row in rows:
            items.append(
                MemoryItem(
                    id=str(row["id"]),
                    type=str(row["type"]),
                    key=row["key"],
                    value=row["value"],
                    canonical_text=row["canonical_text"],
                    confidence=float(row["confidence"]),
                    source_session=row.get("session_id"),
                    source_turn=str(row["source_turn"]) if row.get("source_turn") else None,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    supersedes=str(row["supersedes"]) if row.get("supersedes") else None,
                    superseded_by=str(row["superseded_by"]) if row.get("superseded_by") else None,
                    active=bool(row["active"]),
                    stance=row.get("stance"),
                )
            )
        return MemoriesResponse(memories=items)
    except Exception as exc:
        logger.exception("Failed to get memories for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail=str(exc))
