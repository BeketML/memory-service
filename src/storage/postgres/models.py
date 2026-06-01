from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemoryType(str, enum.Enum):
    fact = "fact"
    preference = "preference"
    opinion = "opinion"
    event = "event"


memory_type_enum = Enum(
    MemoryType,
    name="memory_type",
    create_constraint=True,
    values_callable=lambda x: [e.value for e in x],
)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )

    __table_args__ = (Index("idx_sessions_user", "user_id"),)


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True
    )
    messages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    turn_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )

    __table_args__ = (
        Index("idx_turns_session", "session_id"),
        Index("idx_turns_user_ts", "user_id", "turn_ts"),
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("sessions.session_id", ondelete="SET NULL"), nullable=True
    )
    source_turn: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turns.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[MemoryType] = mapped_column(memory_type_enum, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float, server_default=text("0.7"), nullable=False
    )
    stance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    supersedes: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    superseded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"),
        Index("idx_mem_user_active", "user_id", "active"),
        Index("idx_mem_user_key", "user_id", "key"),
        Index("idx_mem_source_turn", "source_turn"),
        Index(
            "uniq_active_scalar_fact",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("active = true AND type IN ('fact', 'preference')"),
        ),
    )
