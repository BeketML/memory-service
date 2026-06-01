from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response

from src.services.delete import delete_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/users/{user_id}", status_code=204)
async def delete_user_route(user_id: str) -> Response:
    await delete_user(user_id)
    return Response(status_code=204)
