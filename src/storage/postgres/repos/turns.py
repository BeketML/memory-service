from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional
import asyncpg


async def insert_turn(
    conn: asyncpg.Connection,
    session_id: str,
    user_id: Optional[str],
    messages: list,
    raw_text: str,
    turn_ts: datetime,
    metadata: dict,
) -> str:
    turn_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO turns (id, session_id, user_id, messages, raw_text, turn_ts, metadata)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::jsonb)
        """,
        turn_id,
        session_id,
        user_id,
        json.dumps(messages),
        raw_text,
        turn_ts,
        json.dumps(metadata),
    )
    return turn_id


async def get_recent_turns(
    conn: asyncpg.Connection,
    session_id: str,
    limit: int = 5,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, raw_text, turn_ts
        FROM turns
        WHERE session_id = $1
        ORDER BY turn_ts DESC
        LIMIT $2
        """,
        session_id,
        limit,
    )
    return [dict(r) for r in rows]
