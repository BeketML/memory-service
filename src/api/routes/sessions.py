from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response

from src.services.delete import delete_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_route(session_id: str) -> Response:
    await delete_session(session_id)
    return Response(status_code=204)
