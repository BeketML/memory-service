from __future__ import annotations

import logging
from typing import Optional

from src.config import settings
from src.retrieval.embedder import embed
from src.retrieval.qdrant import hybrid_search

logger = logging.getLogger(__name__)


async def search(
    query: str,
    session_id: Optional[str],
    user_id: Optional[str],
    limit: int,
) -> list[dict]:
    try:
        emb = await embed(query)
        results = await hybrid_search(
            emb,
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )
    except Exception as exc:
        logger.warning("Search failed: %s", exc)
        return []

    output = []
    for r in results:
        output.append({
            "content": r.get("canonical_text", ""),
            "score": round(float(r.get("score", 0.0)), 4),
            "session_id": r.get("session_id"),
            "timestamp": r.get("created_at"),
            "metadata": {
                "type": r.get("type", ""),
                "key": r.get("key", ""),
                **r.get("metadata", {}),
            },
        })
    return output
