from __future__ import annotations

import json
from typing import Optional
import asyncpg


async def upsert_user(conn: asyncpg.Connection, user_id: str, metadata: dict) -> None:
    await conn.execute(
        """
        INSERT INTO users (user_id, metadata)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        json.dumps(metadata),
    )


async def delete_user(conn: asyncpg.Connection, user_id: str) -> None:
    await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)


async def user_exists(conn: asyncpg.Connection, user_id: str) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM users WHERE user_id = $1", user_id)
    return row is not None
