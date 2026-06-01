from __future__ import annotations

import json
import uuid
from typing import Optional
import asyncpg


async def get_active_memories(
    conn: asyncpg.Connection,
    user_id: str,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, type, key, value, canonical_text, confidence, stance,
               active, supersedes, superseded_by,
               valid_from, created_at, updated_at, session_id
        FROM memories
        WHERE user_id = $1 AND active = TRUE
        ORDER BY key, created_at
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def get_active_stable_facts(
    conn: asyncpg.Connection,
    user_id: str,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, type, key, value, canonical_text, confidence, stance,
               valid_from, updated_at
        FROM memories
        WHERE user_id = $1 AND active = TRUE
          AND type IN ('fact', 'preference')
        ORDER BY confidence DESC, updated_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def get_active_memory_by_key(
    conn: asyncpg.Connection,
    user_id: str,
    key: str,
) -> Optional[dict]:
    row = await conn.fetchrow(
        """
        SELECT id, type, key, value, canonical_text, confidence, stance,
               active, supersedes, superseded_by
        FROM memories
        WHERE user_id = $1 AND key = $2 AND active = TRUE
        LIMIT 1
        """,
        user_id,
        key,
    )
    return dict(row) if row else None


async def insert_memory(
    conn: asyncpg.Connection,
    user_id: Optional[str],
    session_id: Optional[str],
    source_turn: str,
    type_: str,
    key: str,
    value: str,
    canonical_text: str,
    confidence: float,
    stance: Optional[str],
    supersedes: Optional[str],
    metadata: dict,
) -> str:
    mem_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO memories (
            id, user_id, session_id, source_turn,
            type, key, value, canonical_text, confidence, stance,
            active, supersedes, metadata
        ) VALUES (
            $1, $2, $3, $4,
            $5::memory_type, $6, $7, $8, $9, $10,
            TRUE, $11, $12::jsonb
        )
        """,
        mem_id,
        user_id,
        session_id,
        source_turn,
        type_,
        key,
        value,
        canonical_text,
        confidence,
        stance,
        supersedes,
        json.dumps(metadata),
    )
    return mem_id


async def supersede_memory(
    conn: asyncpg.Connection,
    old_id: str,
    new_id: str,
) -> None:
    await conn.execute(
        """
        UPDATE memories
        SET active = FALSE,
            superseded_by = $2,
            valid_to = now(),
            updated_at = now()
        WHERE id = $1
        """,
        old_id,
        new_id,
    )


async def bump_confidence(
    conn: asyncpg.Connection,
    memory_id: str,
    new_confidence: float,
) -> None:
    await conn.execute(
        """
        UPDATE memories
        SET confidence = $2,
            updated_at = now()
        WHERE id = $1
        """,
        memory_id,
        new_confidence,
    )


async def get_all_memories(
    conn: asyncpg.Connection,
    user_id: str,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, type, key, value, canonical_text, confidence, stance,
               active, supersedes, superseded_by,
               source_turn, session_id,
               valid_from, valid_to, created_at, updated_at, metadata
        FROM memories
        WHERE user_id = $1
        ORDER BY key, created_at
        """,
        user_id,
    )
    return [dict(r) for r in rows]
