from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from src.schemas.recall import RecallRequest, RecallResponse, Citation
from src.services.recall import recall

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/recall")
async def post_recall(body: RecallRequest) -> RecallResponse:
    try:
        result = await recall(
            query=body.query,
            session_id=body.session_id,
            user_id=body.user_id,
            max_tokens=body.max_tokens,
        )
        return RecallResponse(
            context=result["context"],
            citations=[Citation(**c) for c in result["citations"]],
        )
    except Exception as exc:
        logger.exception("Recall failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
