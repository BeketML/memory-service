from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.postgres.models import Session


async def upsert_session(
    session: AsyncSession,
    session_id: str,
    user_id: Optional[str],
    metadata: dict,
) -> None:
    ins = insert(Session).values(
        session_id=session_id,
        user_id=user_id,
        metadata_=metadata,
    )
    stmt = ins.on_conflict_do_update(
        index_elements=["session_id"],
        set_={
            # Use the actual DB column names (not the Python attribute aliases).
            "last_active_at": func.now(),
            "metadata": ins.excluded["metadata"],
        },
    )
    await session.execute(stmt)


async def delete_session(session: AsyncSession, session_id: str) -> None:
    await session.execute(delete(Session).where(Session.session_id == session_id))


async def session_exists(session: AsyncSession, session_id: str) -> bool:
    result = await session.execute(
        select(Session.session_id).where(Session.session_id == session_id).limit(1)
    )
    return result.scalar_one_or_none() is not None
