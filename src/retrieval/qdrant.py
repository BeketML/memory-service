from __future__ import annotations

import logging
from typing import Optional

from qdrant_client.http.models import (
    Filter,
    FieldCondition,
    MatchValue,
    Prefetch,
    FusionQuery,
    Fusion,
    SparseVector,
    PointStruct,
)

from src.config import settings
from src.retrieval.embedder import Embedding
from src.storage.qdrant.collection import get_qdrant_client

logger = logging.getLogger(__name__)


async def upsert_memory(
    memory_id: str,
    embedding: Embedding,
    payload: dict,
) -> None:
    client = get_qdrant_client()
    point = PointStruct(
        id=memory_id,
        vector={
            "dense": embedding.dense,
            "sparse": SparseVector(
                indices=embedding.sparse_indices,
                values=embedding.sparse_values,
            ),
            "colbert": embedding.colbert,
        },
        payload=payload,
    )
    await client.upsert(
        collection_name=settings.qdrant_collection,
        points=[point],
    )


async def patch_payload(memory_id: str, payload_update: dict) -> None:
    client = get_qdrant_client()
    await client.set_payload(
        collection_name=settings.qdrant_collection,
        payload=payload_update,
        points=[memory_id],
    )


async def delete_by_filter(filter_conditions: dict) -> None:
    client = get_qdrant_client()
    from qdrant_client.http.models import FilterSelector
    must = [
        FieldCondition(key=k, match=MatchValue(value=v))
        for k, v in filter_conditions.items()
    ]
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=FilterSelector(filter=Filter(must=must)),
    )


async def hybrid_search(
    embedding: Embedding,
    user_id: Optional[str],
    session_id: Optional[str],
    limit: int,
) -> list[dict]:
    client = get_qdrant_client()

    must = [FieldCondition(key="active", match=MatchValue(value=True))]
    if user_id:
        must.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))
    if session_id and not user_id:
        must.append(FieldCondition(key="session_id", match=MatchValue(value=session_id)))

    query_filter = Filter(must=must)

    try:
        results = await client.query_points(
            collection_name=settings.qdrant_collection,
            prefetch=[
                Prefetch(
                    prefetch=[
                        Prefetch(
                            query=embedding.dense,
                            using="dense",
                            limit=settings.max_candidates,
                            filter=query_filter,
                        ),
                        Prefetch(
                            query=SparseVector(
                                indices=embedding.sparse_indices,
                                values=embedding.sparse_values,
                            ),
                            using="sparse",
                            limit=settings.max_candidates,
                            filter=query_filter,
                        ),
                    ],
                    query=FusionQuery(fusion=Fusion.RRF),
                    limit=settings.rerank_limit,
                )
            ],
            query=embedding.colbert,
            using="colbert",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant hybrid search failed: %s", exc)
        return []

    hits = []
    for point in results.points:
        payload = point.payload or {}
        hits.append({
            "memory_id": payload.get("memory_id", str(point.id)),
            "score": point.score,
            "canonical_text": payload.get("canonical_text", ""),
            "type": payload.get("type", "event"),
            "key": payload.get("key", ""),
            "value": payload.get("value", ""),
            "session_id": payload.get("session_id"),
            "created_at": payload.get("created_at"),
            "metadata": payload.get("metadata", {}),
        })
    return hits
