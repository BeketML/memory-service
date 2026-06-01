from __future__ import annotations

import json
from typing import Optional
import asyncpg


async def upsert_session(
    conn: asyncpg.Connection,
    session_id: str,
    user_id: Optional[str],
    metadata: dict,
) -> None:
    await conn.execute(
        """
        INSERT INTO sessions (session_id, user_id, metadata)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (session_id) DO UPDATE
            SET last_active_at = now(),
                metadata = EXCLUDED.metadata
        """,
        session_id,
        user_id,
        json.dumps(metadata),
    )


async def delete_session(conn: asyncpg.Connection, session_id: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE session_id = $1", session_id)


async def session_exists(conn: asyncpg.Connection, session_id: str) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM sessions WHERE session_id = $1", session_id)
    return row is not None
