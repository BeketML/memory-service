from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.postgres.models import Turn


def _turn_row(row: Turn) -> dict[str, Any]:
    return {
        "id": row.id,
        "raw_text": row.raw_text,
        "turn_ts": row.turn_ts,
    }


async def insert_turn(
    session: AsyncSession,
    session_id: str,
    user_id: Optional[str],
    messages: list,
    raw_text: str,
    turn_ts: datetime,
    metadata: dict,
) -> str:
    turn_id = uuid.uuid4()
    turn = Turn(
        id=turn_id,
        session_id=session_id,
        user_id=user_id,
        messages=messages,
        raw_text=raw_text,
        turn_ts=turn_ts,
        metadata_=metadata,
    )
    session.add(turn)
    await session.flush()
    return str(turn_id)


async def get_recent_turns(
    session: AsyncSession,
    session_id: str,
    limit: int = 5,
) -> list[dict]:
    result = await session.execute(
        select(Turn)
        .where(Turn.session_id == session_id)
        .order_by(Turn.turn_ts.desc())
        .limit(limit)
    )
    return [_turn_row(row) for row in result.scalars().all()]
