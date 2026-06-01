from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Embedding:
    dense: list[float]            # shape [1536] — text-embedding-3-small via OpenAI
    sparse_indices: list[int]     # BM25 term indices
    sparse_values: list[float]    # BM25 term frequencies
    colbert: list[list[float]]    # shape [n_tokens, 128] — colbert-ir/colbertv2.0


@dataclass
class _LocalEmbedding:
    sparse_indices: list[int]
    sparse_values: list[float]
    colbert: list[list[float]]


# ── Local models (fastembed) ─────────────────────────────────────────────────
_sparse_model = None
_colbert_model = None

# ── OpenAI async client (lazily initialised) ─────────────────────────────────
_openai_client = None


def _load_local_models() -> None:
    global _sparse_model, _colbert_model
    if _sparse_model is not None:
        return
    from fastembed import SparseTextEmbedding, LateInteractionTextEmbedding

    logger.info("Loading local embedding models (sparse=%s, colbert=%s)...",
                settings.sparse_model, settings.colbert_model)
    _sparse_model = SparseTextEmbedding(settings.sparse_model)
    _colbert_model = LateInteractionTextEmbedding(settings.colbert_model)
    # warm-up pass to JIT-compile ONNX
    _embed_local("warmup")
    logger.info("Local embedding models loaded.")


def _embed_local(text: str) -> _LocalEmbedding:
    """Synchronous BM25 + ColBERT inference (run in executor)."""
    _load_local_models()
    sparse_result = next(_sparse_model.embed([text]))
    colbert_vecs = next(_colbert_model.embed([text]))
    return _LocalEmbedding(
        sparse_indices=sparse_result.indices.tolist(),
        sparse_values=sparse_result.values.tolist(),
        colbert=[v.tolist() for v in colbert_vecs],
    )


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


async def _embed_dense_openai(text: str) -> list[float]:
    """Call OpenAI embeddings API for the dense vector (1536-d)."""
    client = _get_openai_client()
    resp = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
        encoding_format="float",
    )
    return resp.data[0].embedding


async def embed(text: str) -> Embedding:
    """Embed text using OpenAI (dense) + local fastembed (sparse + colbert).

    The OpenAI API call and local ONNX inference run concurrently via
    asyncio.gather for minimal latency.
    """
    loop = asyncio.get_running_loop()

    # Run dense (async I/O) and local (sync ONNX in thread executor) in parallel
    dense_vec, local = await asyncio.gather(
        _embed_dense_openai(text),
        loop.run_in_executor(None, _embed_local, text),
    )

    return Embedding(
        dense=dense_vec,
        sparse_indices=local.sparse_indices,
        sparse_values=local.sparse_values,
        colbert=local.colbert,
    )


async def load_models() -> None:
    """Pre-load local models at startup (called from lifespan)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _load_local_models)
    logger.info("Embedding models ready (dense=OpenAI/%s, sparse=%s, colbert=%s).",
                settings.openai_embedding_model, settings.sparse_model, settings.colbert_model)
