from __future__ import annotations

import logging

from src.retrieval.qdrant import delete_by_filter
from src.storage.postgres.pool import get_pool
from src.storage.postgres.repos import sessions as sess_repo
from src.storage.postgres.repos import users as user_repo

logger = logging.getLogger(__name__)


async def delete_session(session_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Cascade in PG: session → turns; memories.session_id → NULL
        await sess_repo.delete_session(conn, session_id)

    # Don't delete Qdrant points — memories survive (session_id nulled in PG)
    # Only events that were purely session-scoped (user_id = null) lose their PG anchor
    # The /admin/reindex endpoint can clean up orphaned Qdrant points if needed
    logger.info("Deleted session %s", session_id)


async def delete_user(user_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # CASCADE in PG: user → sessions → turns, memories
        await user_repo.delete_user(conn, user_id)

    # Delete all Qdrant points for this user
    try:
        await delete_by_filter({"user_id": user_id})
    except Exception as exc:
        logger.warning("Qdrant delete for user %s failed: %s", user_id, exc)

    logger.info("Deleted user %s", user_id)
