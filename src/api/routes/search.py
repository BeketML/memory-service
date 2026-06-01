from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from src.schemas.search import SearchRequest, SearchResponse, SearchResult
from src.services.search import search

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/search")
async def post_search(body: SearchRequest) -> SearchResponse:
    try:
        results = await search(
            query=body.query,
            session_id=body.session_id,
            user_id=body.user_id,
            limit=body.limit,
        )
        return SearchResponse(
            results=[SearchResult(**r) for r in results]
        )
    except Exception as exc:
        logger.exception("Search failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
