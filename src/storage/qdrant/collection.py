from __future__ import annotations

import logging
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    Modifier,
    MultiVectorConfig,
    MultiVectorComparator,
    PayloadSchemaType,
)

from src.config import settings

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    if _client is None:
        raise RuntimeError("Qdrant client not initialized")
    return _client


async def init_qdrant() -> None:
    global _client
    _client = AsyncQdrantClient(url=settings.qdrant_url)
    await _ensure_collection()


async def close_qdrant() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None


async def _ensure_collection() -> None:
    client = get_qdrant_client()
    exists = await client.collection_exists(settings.qdrant_collection)

    if not exists:
        await client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config={
                # OpenAI text-embedding-3-small: 1536-d
                "dense": VectorParams(size=settings.dense_dim, distance=Distance.COSINE),
                # colbert-ir/colbertv2.0: 128-d multivector late-interaction
                "colbert": VectorParams(
                    size=settings.colbert_dim,
                    distance=Distance.COSINE,
                    multivector_config=MultiVectorConfig(
                        comparator=MultiVectorComparator.MAX_SIM
                    ),
                ),
            },
            sparse_vectors_config={
                # BM25 with IDF weighting applied by Qdrant at query time
                "sparse": SparseVectorParams(modifier=Modifier.IDF),
            },
        )
        logger.info("Created Qdrant collection '%s' (dense=%d-d, colbert=%d-d)",
                    settings.qdrant_collection, settings.dense_dim, settings.colbert_dim)
    else:
        logger.info(
            "Qdrant collection '%s' already exists — using as-is",
            settings.qdrant_collection,
        )

    # Ensure payload indexes exist (idempotent — safe on both new and existing collections)
    for field, schema in [
        ("user_id", PayloadSchemaType.KEYWORD),
        ("session_id", PayloadSchemaType.KEYWORD),
        ("type", PayloadSchemaType.KEYWORD),
        ("active", PayloadSchemaType.BOOL),
    ]:
        try:
            await client.create_payload_index(
                settings.qdrant_collection, field, schema
            )
        except Exception:
            # Index likely already exists — that's fine
            pass


async def check_healthy() -> bool:
    try:
        client = get_qdrant_client()
        info = await client.get_collection(settings.qdrant_collection)
        return info is not None
    except Exception:
        return False
