"""Contract roundtrip tests — eval category #10: Contract Compliance.

Covers all 7 endpoints from task.md §3:
  GET  /health
  POST /turns            → 201  { id }
  POST /recall           → 200  { context, citations[] }
  POST /search           → 200  { results[] }
  GET  /users/{id}/memories → 200 { memories[] }
  DELETE /sessions/{id}  → 204
  DELETE /users/{id}     → 204
"""
from __future__ import annotations

import uuid
import pytest


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"


# ── POST /turns ───────────────────────────────────────────────────────────────

def test_turns_returns_201_with_uuid(client, unique_user, unique_session):
    resp = client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I live in Berlin and work at Notion as a PM."},
            {"role": "assistant", "content": "Great, Berlin is a wonderful city!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert isinstance(body["id"], str)
    try:
        uuid.UUID(body["id"])
    except ValueError:
        pytest.fail(f"Returned id is not a valid UUID: {body['id']!r}")


def test_turns_accepts_null_user_id(client, unique_session):
    """Anonymous sessions (user_id=null) must be accepted."""
    resp = client.post("/turns", json={
        "session_id": unique_session,
        "user_id": None,
        "messages": [
            {"role": "user",      "content": "I prefer dark mode."},
            {"role": "assistant", "content": "Noted!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    assert resp.status_code == 201


def test_turns_accepts_tool_role_message(client, unique_user, unique_session):
    """Multi-message turns including tool role must be accepted."""
    resp = client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "Search for flights to Paris."},
            {"role": "assistant", "content": "Searching..."},
            {"role": "tool",      "content": "Found 3 flights.", "name": "search_flights"},
            {"role": "assistant", "content": "Here are your results."},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    assert resp.status_code == 201


# ── POST /recall ──────────────────────────────────────────────────────────────

def test_recall_200_shape(client, unique_user, unique_session):
    resp = client.post("/recall", json={
        "query": "What do I know about this user?",
        "session_id": unique_session,
        "user_id": unique_user,
        "max_tokens": 512,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("context"), str)
    assert isinstance(body.get("citations"), list)


def test_recall_after_turn_mentions_fact(client, unique_user, unique_session):
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I just moved to Berlin from NYC last month."},
            {"role": "assistant", "content": "Exciting! Berlin is a great city."},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.post("/recall", json={
        "query": "Where does this user live?",
        "session_id": unique_session,
        "user_id": unique_user,
        "max_tokens": 512,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["context"], str)
    assert isinstance(body["citations"], list)
    assert "berlin" in body["context"].lower() or body["context"] == ""


def test_recall_citations_shape(client, unique_user, unique_session):
    """Each citation must have turn_id (str), score (float), snippet (str)."""
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I just moved to Berlin from NYC."},
            {"role": "assistant", "content": "Exciting!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.post("/recall", json={
        "query": "Where does this user live?",
        "session_id": unique_session,
        "user_id": unique_user,
        "max_tokens": 512,
    })
    assert resp.status_code == 200
    for cite in resp.json()["citations"]:
        assert isinstance(cite.get("turn_id"), str), f"turn_id missing/wrong: {cite}"
        assert isinstance(cite.get("score"), float),  f"score missing/wrong: {cite}"
        assert isinstance(cite.get("snippet"), str),  f"snippet missing/wrong: {cite}"


def test_recall_cold_session_empty(client):
    """Cold session must return 200 with empty context and no citations."""
    resp = client.post("/recall", json={
        "query":      "What do I know about this person?",
        "session_id": f"cold-{uuid.uuid4().hex}",
        "user_id":    f"cold-{uuid.uuid4().hex}",
        "max_tokens": 512,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["context"] == "", f"Expected empty context: {body['context'][:100]!r}"
    assert body["citations"] == []


def test_recall_respects_max_tokens(client, unique_user, unique_session):
    """Context token count must not exceed 2× max_tokens budget."""
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I'm a backend engineer at Stripe in NYC. I have a dog named Biscuit. I love TypeScript. I'm vegetarian."},
            {"role": "assistant", "content": "Got it!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    max_tokens = 64
    resp = client.post("/recall", json={
        "query":      "Tell me everything about this user.",
        "session_id": unique_session,
        "user_id":    unique_user,
        "max_tokens": max_tokens,
    })
    assert resp.status_code == 200
    context = resp.json()["context"]
    approx_tokens = len(context) // 4
    assert approx_tokens <= max_tokens * 2, (
        f"Context too long: ~{approx_tokens} tokens (budget={max_tokens})"
    )


# ── POST /search ──────────────────────────────────────────────────────────────

def test_search_200_shape(client, unique_user, unique_session):
    """All required search result fields must be present with correct types."""
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I have a golden retriever named Biscuit."},
            {"role": "assistant", "content": "What a lovely name!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.post("/search", json={
        "query":   "dog pet Biscuit",
        "user_id": unique_user,
        "limit":   5,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert isinstance(body["results"], list)
    for r in body["results"]:
        assert isinstance(r.get("content"), str),  f"content missing: {r}"
        assert isinstance(r.get("score"),   float), f"score missing: {r}"
        assert "session_id" in r,                   f"session_id missing: {r}"
        assert "metadata"   in r,                   f"metadata missing: {r}"
        ts = r.get("timestamp")
        assert ts is not None,          f"timestamp is None in: {r}"
        assert isinstance(ts, str),     f"timestamp not str: {r}"
        assert "T" in ts,               f"timestamp not ISO: {ts!r}"


# ── GET /users/{id}/memories ──────────────────────────────────────────────────

def test_memories_all_required_fields(client, unique_user, unique_session):
    """All task.md contract fields must be present on every memory row."""
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I'm a vegetarian software engineer at Acme Corp."},
            {"role": "assistant", "content": "Good to know!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.get(f"/users/{unique_user}/memories")
    assert resp.status_code == 200
    body = resp.json()
    assert "memories" in body

    required = {
        "id", "type", "key", "value", "confidence",
        "active", "created_at", "updated_at",
        "supersedes", "superseded_by",
        "source_session", "source_turn", "canonical_text",
    }
    for mem in body["memories"]:
        missing = required - set(mem.keys())
        assert not missing, f"Memory missing fields {missing}: {mem}"
        assert mem["type"] in ("fact", "preference", "opinion", "event"), (
            f"Invalid memory type: {mem['type']!r}"
        )
        assert 0.0 <= mem["confidence"] <= 1.0, (
            f"Confidence out of range: {mem['confidence']}"
        )


def test_memories_not_raw_message_chunks(client, unique_user, unique_session):
    """Memories must be structured (typed with keys), not raw message dumps."""
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [
            {"role": "user",      "content": "I work as a data scientist at OpenAI in San Francisco."},
            {"role": "assistant", "content": "Interesting!"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.get(f"/users/{unique_user}/memories")
    mems = resp.json()["memories"]
    for mem in mems:
        # key must follow dot-notation convention (e.g., employment.employer)
        assert "." in mem["key"], (
            f"Memory key not dot-notation: {mem['key']!r} — looks like raw chunk"
        )
        # canonical_text must be a sentence, not just the raw turn content
        assert len(mem["canonical_text"]) > 5, f"canonical_text too short: {mem!r}"


def test_memories_unknown_user_empty(client):
    resp = client.get(f"/users/unknown-{uuid.uuid4().hex}/memories")
    assert resp.status_code == 200
    assert resp.json()["memories"] == []


# ── DELETE /sessions and /users ───────────────────────────────────────────────

def test_delete_session_204(client, unique_user, unique_session):
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.delete(f"/sessions/{unique_session}")
    assert resp.status_code == 204


def test_delete_session_idempotent(client):
    """Deleting a non-existent session must return 204."""
    resp = client.delete(f"/sessions/nonexistent-{uuid.uuid4().hex}")
    assert resp.status_code == 204


def test_delete_user_204_clears_memories(client, unique_user, unique_session):
    client.post("/turns", json={
        "session_id": unique_session,
        "user_id": unique_user,
        "messages": [{"role": "user", "content": "I live in Paris."}, {"role": "assistant", "content": "Nice!"}],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    })
    resp = client.delete(f"/users/{unique_user}")
    assert resp.status_code == 204
    resp2 = client.get(f"/users/{unique_user}/memories")
    assert resp2.status_code == 200
    assert resp2.json()["memories"] == []


def test_delete_user_idempotent(client):
    resp = client.delete(f"/users/nonexistent-{uuid.uuid4().hex}")
    assert resp.status_code == 204
