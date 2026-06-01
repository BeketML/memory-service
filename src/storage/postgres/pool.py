from __future__ import annotations

import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

SCHEMA_SQL = """
DO $$ BEGIN
    CREATE TYPE memory_type AS ENUM ('fact', 'preference', 'opinion', 'event');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS turns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id      TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    messages     JSONB        NOT NULL,
    raw_text     TEXT         NOT NULL,
    turn_ts      TIMESTAMPTZ  NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    metadata     JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_turns_session  ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_user_ts  ON turns(user_id, turn_ts DESC);

CREATE TABLE IF NOT EXISTS memories (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT        REFERENCES users(user_id)       ON DELETE CASCADE,
    session_id     TEXT        REFERENCES sessions(session_id) ON DELETE SET NULL,
    source_turn    UUID        REFERENCES turns(id)            ON DELETE CASCADE,

    type           memory_type NOT NULL,
    key            TEXT        NOT NULL,
    value          TEXT        NOT NULL,
    canonical_text TEXT        NOT NULL,
    confidence     REAL        NOT NULL DEFAULT 0.7
                               CHECK (confidence BETWEEN 0 AND 1),
    stance         TEXT,

    active         BOOLEAN     NOT NULL DEFAULT TRUE,
    supersedes     UUID        REFERENCES memories(id) ON DELETE SET NULL,
    superseded_by  UUID        REFERENCES memories(id) ON DELETE SET NULL,

    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_mem_user_active ON memories(user_id, active);
CREATE INDEX IF NOT EXISTS idx_mem_user_key    ON memories(user_id, key);
CREATE INDEX IF NOT EXISTS idx_mem_source_turn ON memories(source_turn);

DO $$ BEGIN
    CREATE UNIQUE INDEX uniq_active_scalar_fact
        ON memories(user_id, key)
        WHERE active = TRUE AND type IN ('fact', 'preference');
EXCEPTION WHEN duplicate_table THEN NULL;
END $$;
"""


async def init_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


async def check_healthy() -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False
