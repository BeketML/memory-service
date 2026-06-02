from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from src.evolution.reconcile import reconcile_candidate
from src.extraction.llm import call_llm
from src.extraction.parser import Candidate, make_fallback_candidate, parse_candidates
from src.extraction.prompts import EXTRACTION_SYSTEM, EXTRACTION_USER_TEMPLATE
from src.retrieval.embedder import embed
from src.retrieval.qdrant import patch_payload, upsert_memory
from src.storage.postgres.database import get_session
from src.storage.postgres.repos import memories as mem_repo
from src.storage.postgres.repos import sessions as sess_repo
from src.storage.postgres.repos import turns as turn_repo
from src.storage.postgres.repos import users as user_repo

logger = logging.getLogger(__name__)

_MAX_RAW_TEXT = 16_000  # chars, truncated before extraction


def _flatten_messages(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        name = msg.get("name")
        if name:
            parts.append(f"tool[{name}]: {content}")
        else:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _format_known_state(memories: list[dict]) -> str:
    if not memories:
        return "No memories yet."
    items = [
        {"key": m["key"], "value": m["value"], "type": m["type"]}
        for m in memories
    ]
    return json.dumps(items, indent=2)


async def ingest_turn(
    session_id: str,
    user_id: Optional[str],
    messages: list[dict],
    turn_ts: datetime,
    metadata: dict,
) -> str:
    async with get_session() as session:
        if user_id:
            await user_repo.upsert_user(session, user_id, {})
        await sess_repo.upsert_session(session, session_id, user_id, metadata)

        raw_text = _flatten_messages(messages)
        if len(raw_text) > _MAX_RAW_TEXT:
            raw_text = raw_text[:_MAX_RAW_TEXT] + "\n[truncated]"

        turn_id = await turn_repo.insert_turn(
            session,
            session_id=session_id,
            user_id=user_id,
            messages=messages,
            raw_text=raw_text,
            turn_ts=turn_ts,
            metadata=metadata,
        )

        known_memories: list[dict] = []
        if user_id:
            known_memories = await mem_repo.get_active_memories(session, user_id)

    # LLM extraction (outside DB connection — can take up to ~30s)
    candidates: list[Candidate] = []
    try:
        prompt = EXTRACTION_USER_TEMPLATE.format(
            known_state=_format_known_state(known_memories),
            raw_text=raw_text,
        )
        raw_output = await call_llm(EXTRACTION_SYSTEM, prompt)
        candidates = parse_candidates(raw_output)
        logger.info("Extracted %d candidate(s) from turn %s", len(candidates), turn_id)
    except Exception as exc:
        logger.warning("LLM extraction failed for turn %s: %s — using fallback", turn_id, exc)
        candidates = [make_fallback_candidate(raw_text, turn_id)]

    if not candidates:
        return turn_id

    # Reconcile + embed + upsert Qdrant per candidate.
    # Each candidate is wrapped in a SAVEPOINT so a failure on one candidate
    # rolls back only that savepoint and the outer transaction continues.
    async with get_session() as session:
        for candidate in candidates:
            try:
                async with session.begin_nested():   # SAVEPOINT
                    new_mem_id, superseded_id = await reconcile_candidate(
                        session,
                        candidate,
                        user_id=user_id,
                        session_id=session_id,
                        source_turn=turn_id,
                    )

                if new_mem_id is None:
                    continue  # same value restated — no new Qdrant point

                emb = await embed(candidate.canonical_text)
                payload = {
                    "memory_id": new_mem_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "type": candidate.type,
                    "key": candidate.key,
                    "value": candidate.value,
                    "canonical_text": candidate.canonical_text,
                    "confidence": candidate.confidence,
                    "active": True,
                    "source_turn_id": turn_id,
                    # ISO-8601 creation time so /search can return a real timestamp.
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await upsert_memory(new_mem_id, emb, payload)

                if superseded_id:
                    # Mark the old Qdrant point as inactive (kept for history/search)
                    await patch_payload(superseded_id, {"active": False})

            except Exception as exc:
                logger.warning(
                    "Failed to process candidate key=%s: %s",
                    candidate.key,
                    exc,
                )

    return turn_id
