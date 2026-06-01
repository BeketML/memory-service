from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Embedding:
    dense: list[float]            # shape [1024]
    sparse_indices: list[int]
    sparse_values: list[float]
    colbert: list[list[float]]    # shape [n_tokens, 1024]


_dense_model = None
_sparse_model = None
_colbert_model = None


def _load_models() -> None:
    global _dense_model, _sparse_model, _colbert_model
    if _dense_model is not None:
        return
    from fastembed import TextEmbedding, SparseTextEmbedding, LateInteractionTextEmbedding

    model_name = settings.embedding_model
    logger.info("Loading embedding models (%s)...", model_name)
    _dense_model = TextEmbedding(model_name)
    _sparse_model = SparseTextEmbedding(model_name)
    _colbert_model = LateInteractionTextEmbedding(model_name)
    # warm-up pass to JIT-compile
    _embed_sync("warmup")
    logger.info("Embedding models loaded.")


def _embed_sync(text: str) -> Embedding:
    import numpy as np

    dense_vec = next(_dense_model.embed([text]))
    sparse_result = next(_sparse_model.embed([text]))
    colbert_vecs = next(_colbert_model.embed([text]))

    return Embedding(
        dense=dense_vec.tolist(),
        sparse_indices=sparse_result.indices.tolist(),
        sparse_values=sparse_result.values.tolist(),
        colbert=[v.tolist() for v in colbert_vecs],
    )


async def embed(text: str) -> Embedding:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_sync, text)


async def load_models() -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_models)
