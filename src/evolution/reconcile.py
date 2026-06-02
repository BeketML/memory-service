from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.extraction.parser import Candidate
from src.storage.postgres.repos import memories as mem_repo

# The partial unique index `uniq_active_scalar_fact` enforces at most one active
# fact/preference per (user_id, key).  When superseding, we must deactivate the
# old row and flush BEFORE inserting the new one, otherwise the INSERT races
# against the still-active old row and hits a UniqueViolationError.

logger = logging.getLogger(__name__)


def _normalize_value(v: str) -> str:
    return v.strip().lower()


async def reconcile_candidate(
    session: AsyncSession,
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
            session,
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

    existing = await mem_repo.get_active_memory_by_key(session, user_id, candidate.key)

    if existing is None:
        new_id = await mem_repo.insert_memory(
            session,
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
        await mem_repo.bump_confidence(session, str(existing["id"]), new_conf)
        return None, None

    metadata = {}
    if candidate.operation == "correct":
        metadata["correction"] = True

    old_id = str(existing["id"])

    # Step 1 — deactivate old row and flush so the unique-index slot is free.
    await mem_repo.deactivate_memory(session, old_id)
    await session.flush()

    # Step 2 — insert new memory (now no active row with this key exists).
    new_id = await mem_repo.insert_memory(
        session,
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

    # Step 3 — wire the back-reference now that new_id is known.
    await mem_repo.set_superseded_by(session, old_id, new_id)

    logger.info(
        "Superseded %s (key=%s, %r → %r)",
        old_id,
        candidate.key,
        existing["value"],
        candidate.value,
    )
    return new_id, old_id
