from __future__ import annotations

import logging

from src.retrieval.qdrant import delete_by_filter
from src.storage.postgres.database import get_session
from src.storage.postgres.repos import sessions as sess_repo
from src.storage.postgres.repos import users as user_repo

logger = logging.getLogger(__name__)


async def delete_session(session_id: str) -> None:
    async with get_session() as session:
        await sess_repo.delete_session(session, session_id)

    # Remove Qdrant points sourced from this session.
    # Memories are user-scoped in PG (session_id SET NULL on cascade), so the
    # PG rows survive; we mirror that by keeping the Qdrant points too — only
    # the session row and its turns are gone. No Qdrant delete needed here.
    # (Points remain active=true and are still findable via user_id filter.)

    logger.info("Deleted session %s", session_id)


async def delete_user(user_id: str) -> None:
    async with get_session() as session:
        await user_repo.delete_user(session, user_id)

    try:
        await delete_by_filter({"user_id": user_id})
    except Exception as exc:
        logger.warning("Qdrant delete for user %s failed: %s", user_id, exc)

    logger.info("Deleted user %s", user_id)
