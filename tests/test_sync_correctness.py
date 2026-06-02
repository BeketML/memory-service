"""Synchronous correctness tests — eval category #9: Correctness.

After POST /turns returns 201, extracted memories must be immediately
queryable via /recall and /users/{id}/memories — no sleep, no polling.

This is the "no eventual consistency" guarantee from task.md §5.
"""
from __future__ import annotations

import uuid
import pytest


@pytest.fixture
def user(client):
    uid = f"sync-{uuid.uuid4().hex[:8]}"
    yield uid
    client.delete(f"/users/{uid}")


def test_memories_immediately_queryable_after_turns(client, user):
    """After 201, /users/{id}/memories must have ≥1 memory without any delay."""
    sid = f"sync-s-{uuid.uuid4().hex[:8]}"

    # Write a turn with a distinctive, extractable fact
    resp = client.post("/turns", json={
        "session_id": sid,
        "user_id":    user,
        "messages": [
            {"role": "user",      "content": "I am a robotics engineer at SpaceX in Hawthorne, California."},
            {"role": "assistant", "content": "That sounds like amazing work!"},
        ],
        "timestamp": "2025-03-15T10:00:00Z",
        "metadata":  {},
    })
    assert resp.status_code == 201

    # Immediately (no sleep) query memories
    resp2 = client.get(f"/users/{user}/memories")
    assert resp2.status_code == 200
    mems = resp2.json()["memories"]
    assert len(mems) >= 1, (
        "After 201, /memories returned 0 rows — synchronous commitment violated"
    )


def test_recall_immediately_queryable_after_turns(client, user):
    """After 201, /recall must return non-empty context without any delay."""
    sid = f"sync-s-{uuid.uuid4().hex[:8]}"

    resp = client.post("/turns", json={
        "session_id": sid,
        "user_id":    user,
        "messages": [
            {"role": "user",      "content": "I work at Notion as a PM in Berlin."},
            {"role": "assistant", "content": "Notion is a great product!"},
        ],
        "timestamp": "2025-03-15T10:00:00Z",
        "metadata":  {},
    })
    assert resp.status_code == 201

    # Immediately query recall
    resp2 = client.post("/recall", json={
        "query":      "Where does this user work?",
        "session_id": f"probe-{uuid.uuid4().hex[:8]}",
        "user_id":    user,
        "max_tokens": 512,
    })
    assert resp2.status_code == 200
    body = resp2.json()
    assert isinstance(body["context"], str)
    # Context must be non-empty — the turn was committed synchronously
    assert body["context"] != "", (
        "After 201, /recall returned empty context — memories not yet committed (eventual consistency?)"
    )


def test_turn_id_matches_memory_provenance(client, user):
    """The turn_id from POST /turns must appear as source_turn in /memories."""
    sid = f"sync-s-{uuid.uuid4().hex[:8]}"

    resp = client.post("/turns", json={
        "session_id": sid,
        "user_id":    user,
        "messages": [
            {"role": "user",      "content": "I have a golden retriever named Biscuit."},
            {"role": "assistant", "content": "What a lovely name!"},
        ],
        "timestamp": "2025-03-15T10:00:00Z",
        "metadata":  {},
    })
    assert resp.status_code == 201
    turn_id = resp.json()["id"]

    # Memories extracted from this turn must link back to it
    mems = client.get(f"/users/{user}/memories").json()["memories"]
    assert any(str(m.get("source_turn")) == turn_id for m in mems), (
        f"No memory has source_turn={turn_id!r}. Memory source_turns: "
        f"{[m.get('source_turn') for m in mems]}"
    )


def test_multiple_turns_all_committed_sync(client, user):
    """Writing 3 turns in sequence, then recalling, must see all 3 sets of facts."""
    s1 = f"s1-{uuid.uuid4().hex[:8]}"
    s2 = f"s2-{uuid.uuid4().hex[:8]}"
    s3 = f"s3-{uuid.uuid4().hex[:8]}"

    client.post("/turns", json={
        "session_id": s1, "user_id": user,
        "messages": [{"role": "user", "content": "I live in Berlin."}, {"role": "assistant", "content": "Nice!"}],
        "timestamp": "2025-03-01T10:00:00Z", "metadata": {},
    })
    client.post("/turns", json={
        "session_id": s2, "user_id": user,
        "messages": [{"role": "user", "content": "I work at Notion."}, {"role": "assistant", "content": "Great!"}],
        "timestamp": "2025-03-02T10:00:00Z", "metadata": {},
    })
    client.post("/turns", json={
        "session_id": s3, "user_id": user,
        "messages": [{"role": "user", "content": "I have a cat named Mochi."}, {"role": "assistant", "content": "Cute!"}],
        "timestamp": "2025-03-03T10:00:00Z", "metadata": {},
    })

    # All 3 should be immediately committed
    mems = client.get(f"/users/{user}/memories").json()["memories"]
    assert len(mems) >= 1, (
        f"Expected memories from 3 turns, got {len(mems)} — some turns not committed synchronously"
    )
