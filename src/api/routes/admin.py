from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.retrieval.embedder import embed
from src.retrieval.qdrant import upsert_memory
from src.storage.postgres.database import get_session
from src.storage.postgres.models import Memory

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/admin/reindex")
async def reindex() -> JSONResponse:
    """Rebuild the Qdrant index from PostgreSQL (source of truth).

    Re-upserts all memories without deleting the collection — safe to call
    while the service is running, safe for pre-existing collections.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Memory).order_by(Memory.created_at)
        )
        rows = result.scalars().all()

    count = 0
    for row in rows:
        try:
            emb = await embed(row.canonical_text)
            payload = {
                "memory_id": str(row.id),
                "user_id": row.user_id,
                "session_id": row.session_id,
                "type": row.type.value if hasattr(row.type, "value") else str(row.type),
                "key": row.key,
                "value": row.value,
                "canonical_text": row.canonical_text,
                "confidence": float(row.confidence),
                "active": bool(row.active),
                "source_turn_id": str(row.source_turn) if row.source_turn else None,
            }
            await upsert_memory(str(row.id), emb, payload)
            count += 1
        except Exception as exc:
            logger.warning("Failed to reindex memory %s: %s", row.id, exc)

    return JSONResponse({"reindexed": count})
