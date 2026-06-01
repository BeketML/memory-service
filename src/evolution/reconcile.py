from __future__ import annotations

import logging
from typing import Optional
import asyncpg

from src.extraction.parser import Candidate
from src.storage.postgres.repos import memories as mem_repo

logger = logging.getLogger(__name__)


def _normalize_value(v: str) -> str:
    return v.strip().lower()


async def reconcile_candidate(
    conn: asyncpg.Connection,
    candidate: Candidate,
    user_id: Optional[str],
    session_id: Optional[str],
    source_turn: str,
) -> tuple[Optional[str], Optional[str]]:
    """Reconcile a candidate against current DB state.

    Returns (new_memory_id, superseded_memory_id).
    new_memory_id is None when the value was restated (no new row).
    superseded_memory_id is the old memory ID that was deactivated, or None.
    """
    if not user_id:
        new_id = await mem_repo.insert_memory(
            conn,
            user_id=None,
            session_id=session_id,
            source_turn=source_turn,
            type_=candidate.type,
            key=candidate.key,
            value=candidate.value,
            canonical_text=candidate.canonical_text,
            confidence=candidate.confidence,
            stance=candidate.stance,
            supersedes=None,
            metadata={},
        )
        return new_id, None

    existing = await mem_repo.get_active_memory_by_key(conn, user_id, candidate.key)

    if existing is None:
        new_id = await mem_repo.insert_memory(
            conn,
            user_id=user_id,
            session_id=session_id,
            source_turn=source_turn,
            type_=candidate.type,
            key=candidate.key,
            value=candidate.value,
            canonical_text=candidate.canonical_text,
            confidence=candidate.confidence,
            stance=candidate.stance,
            supersedes=None,
            metadata={},
        )
        return new_id, None

    if _normalize_value(existing["value"]) == _normalize_value(candidate.value):
        new_conf = max(existing["confidence"], candidate.confidence)
        await mem_repo.bump_confidence(conn, str(existing["id"]), new_conf)
        return None, None

    metadata = {}
    if candidate.operation == "correct":
        metadata["correction"] = True

    old_id = str(existing["id"])
    new_id = await mem_repo.insert_memory(
        conn,
        user_id=user_id,
        session_id=session_id,
        source_turn=source_turn,
        type_=candidate.type,
        key=candidate.key,
        value=candidate.value,
        canonical_text=candidate.canonical_text,
        confidence=candidate.confidence,
        stance=candidate.stance,
        supersedes=old_id,
        metadata=metadata,
    )
    await mem_repo.supersede_memory(conn, old_id, new_id)
    logger.info(
        "Superseded %s (key=%s, %r → %r)",
        old_id,
        candidate.key,
        existing["value"],
        candidate.value,
    )
    return new_id, old_id
