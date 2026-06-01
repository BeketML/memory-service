from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config import settings
from src.retrieval.embedder import embed
from src.retrieval.qdrant import upsert_memory
from src.storage.postgres.pool import get_pool
from src.storage.qdrant.collection import get_qdrant_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/admin/reindex")
async def reindex() -> JSONResponse:
    """Rebuild the Qdrant index from PostgreSQL (source of truth).

    Re-upserts all memories without deleting the collection — safe to call
    while the service is running, safe for pre-existing collections.
    """
    pool = await get_pool()
    client = get_qdrant_client()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, session_id, source_turn,
                   type, key, value, canonical_text, confidence,
                   active, stance
            FROM memories
            ORDER BY created_at
            """
        )

    count = 0
    for row in rows:
        try:
            emb = await embed(row["canonical_text"])
            payload = {
                "memory_id": str(row["id"]),
                "user_id": row["user_id"],
                "session_id": row["session_id"],
                "type": str(row["type"]),
                "key": row["key"],
                "value": row["value"],
                "canonical_text": row["canonical_text"],
                "confidence": float(row["confidence"]),
                "active": bool(row["active"]),
                "source_turn_id": str(row["source_turn"]) if row["source_turn"] else None,
            }
            await upsert_memory(str(row["id"]), emb, payload)
            count += 1
        except Exception as exc:
            logger.warning("Failed to reindex memory %s: %s", row["id"], exc)

    return JSONResponse({"reindexed": count})
