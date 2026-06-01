from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.schemas.turns import TurnRequest, TurnResponse
from src.services.ingest import ingest_turn

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/turns", status_code=201)
async def post_turn(body: TurnRequest) -> TurnResponse:
    try:
        messages = [m.model_dump() for m in body.messages]
        turn_id = await ingest_turn(
            session_id=body.session_id,
            user_id=body.user_id,
            messages=messages,
            turn_ts=body.timestamp,
            metadata=body.metadata,
        )
        return TurnResponse(id=turn_id)
    except Exception as exc:
        logger.exception("Failed to ingest turn: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
