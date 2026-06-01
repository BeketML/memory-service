from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.postgres.models import Memory, MemoryType

def _memory_row(row: Memory, fields: str = "full") -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": row.id,
        "type": row.type.value if isinstance(row.type, MemoryType) else row.type,
        "key": row.key,
        "value": row.value,
        "canonical_text": row.canonical_text,
        "confidence": row.confidence,
        "stance": row.stance,
    }
    if fields == "stable":
        base["valid_from"] = row.valid_from
        base["updated_at"] = row.updated_at
        return base
    if fields == "active":
        base.update(
            {
                "active": row.active,
                "supersedes": row.supersedes,
                "superseded_by": row.superseded_by,
                "valid_from": row.valid_from,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "session_id": row.session_id,
            }
        )
        return base
    base.update(
        {
            "active": row.active,
            "supersedes": row.supersedes,
            "superseded_by": row.superseded_by,
            "source_turn": row.source_turn,
            "session_id": row.session_id,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "metadata": row.metadata_,
        }
    )
    return base


async def get_active_memories(session: AsyncSession, user_id: str) -> list[dict]:
    result = await session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.active.is_(True))
        .order_by(Memory.key, Memory.created_at)
    )
    return [_memory_row(row, "active") for row in result.scalars().all()]


async def get_active_stable_facts(session: AsyncSession, user_id: str) -> list[dict]:
    result = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.active.is_(True),
            Memory.type.in_([MemoryType.fact, MemoryType.preference]),
        )
        .order_by(Memory.confidence.desc(), Memory.updated_at.desc())
    )
    return [_memory_row(row, "stable") for row in result.scalars().all()]


async def get_active_memory_by_key(
    session: AsyncSession,
    user_id: str,
    key: str,
) -> Optional[dict]:
    result = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.key == key,
            Memory.active.is_(True),
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return _memory_row(row, "active") if row else None


async def insert_memory(
    session: AsyncSession,
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
    mem_type = MemoryType(type_)
    source_turn_uuid = uuid.UUID(source_turn) if source_turn else None
    supersedes_uuid = uuid.UUID(supersedes) if supersedes else None

    memory = Memory(
        user_id=user_id,
        session_id=session_id,
        source_turn=source_turn_uuid,
        type=mem_type,
        key=key,
        value=value,
        canonical_text=canonical_text,
        confidence=confidence,
        stance=stance,
        active=True,
        supersedes=supersedes_uuid,
        metadata_=metadata,
    )
    session.add(memory)
    await session.flush()
    return str(memory.id)


async def supersede_memory(
    session: AsyncSession,
    old_id: str,
    new_id: str,
) -> None:
    await session.execute(
        update(Memory)
        .where(Memory.id == uuid.UUID(old_id))
        .values(
            active=False,
            superseded_by=uuid.UUID(new_id),
            valid_to=func.now(),
            updated_at=func.now(),
        )
    )


async def bump_confidence(
    session: AsyncSession,
    memory_id: str,
    new_confidence: float,
) -> None:
    await session.execute(
        update(Memory)
        .where(Memory.id == uuid.UUID(memory_id))
        .values(confidence=new_confidence, updated_at=func.now())
    )


async def get_all_memories(session: AsyncSession, user_id: str) -> list[dict]:
    result = await session.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.key, Memory.created_at)
    )
    return [_memory_row(row, "full") for row in result.scalars().all()]
