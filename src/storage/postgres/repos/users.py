from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.postgres.models import User


async def upsert_user(session: AsyncSession, user_id: str, metadata: dict) -> None:
    stmt = (
        insert(User)
        .values(user_id=user_id, metadata_=metadata)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await session.execute(stmt)


async def delete_user(session: AsyncSession, user_id: str) -> None:
    user = await session.get(User, user_id)
    if user is not None:
        await session.delete(user)


async def user_exists(session: AsyncSession, user_id: str) -> bool:
    result = await session.execute(
        select(User.user_id).where(User.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None
