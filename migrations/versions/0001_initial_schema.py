"""Initial schema — users, sessions, turns, memories.

Revision ID: 0001
Revises: (none)
Create Date: 2025-06-02

Idempotency: if the 'memories' table already exists (schema was previously
created by Base.metadata.create_all) this migration records itself as applied
without running any DDL, so 'alembic upgrade head' is safe on both a live
dev database and a brand-new empty volume.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _already_migrated(conn: sa.engine.Connection) -> bool:
    """Return True if the schema was already created outside of Alembic."""
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_schema = 'public' AND table_name = 'memories'"
            ")"
        )
    )
    return bool(result.scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if _already_migrated(bind):
        # Tables were created by Base.metadata.create_all; nothing to do.
        # Alembic will still stamp the alembic_version row.
        return

    # ── extensions ────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ── sessions ──────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Text, primary_key=True),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("idx_sessions_user", "sessions", ["user_id"])

    # ── turns ─────────────────────────────────────────────────────────────────
    op.create_table(
        "turns",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            sa.Text,
            sa.ForeignKey("sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("messages", JSONB, nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("turn_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("idx_turns_session", "turns", ["session_id"])
    op.create_index("idx_turns_user_ts", "turns", ["user_id", "turn_ts"])

    # ── memory_type enum ──────────────────────────────────────────────────────
    memory_type = sa.Enum(
        "fact", "preference", "opinion", "event",
        name="memory_type",
        create_type=True,
    )

    # ── memories ──────────────────────────────────────────────────────────────
    op.create_table(
        "memories",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Text,
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.Text,
            sa.ForeignKey("sessions.session_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_turn",
            UUID(as_uuid=True),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("type", memory_type, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("canonical_text", sa.Text, nullable=False),
        sa.Column(
            "confidence",
            sa.Float,
            nullable=False,
            server_default=sa.text("0.7"),
        ),
        sa.Column("stance", sa.Text, nullable=True),
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "supersedes",
            UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "superseded_by",
            UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memories_confidence",
        ),
    )

    # Standard indexes
    op.create_index("idx_mem_user_active", "memories", ["user_id", "active"])
    op.create_index("idx_mem_user_key", "memories", ["user_id", "key"])
    op.create_index("idx_mem_source_turn", "memories", ["source_turn"])

    # Partial unique index — at most one active fact/preference per (user, key).
    # This physically enforces fact-evolution integrity.
    op.create_index(
        "uniq_active_scalar_fact",
        "memories",
        ["user_id", "key"],
        unique=True,
        postgresql_where=sa.text("active = true AND type IN ('fact', 'preference')"),
    )


def downgrade() -> None:
    op.drop_index("uniq_active_scalar_fact", table_name="memories")
    op.drop_index("idx_mem_source_turn", table_name="memories")
    op.drop_index("idx_mem_user_key", table_name="memories")
    op.drop_index("idx_mem_user_active", table_name="memories")
    op.drop_table("memories")
    # Drop the enum type after the table that uses it
    sa.Enum(name="memory_type").drop(op.get_bind(), checkfirst=True)

    op.drop_index("idx_turns_user_ts", table_name="turns")
    op.drop_index("idx_turns_session", table_name="turns")
    op.drop_table("turns")

    op.drop_index("idx_sessions_user", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("users")
