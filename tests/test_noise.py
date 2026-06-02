"""Noise resistance tests — eval category #4: Noise Resistance.

Queries about topics never discussed must return empty context (no hallucination).
Cold sessions must return 200 with empty context and citations.
"""
from __future__ import annotations

import uuid
import pytest


@pytest.fixture
def seeded_user(client):
    """User who has only discussed Python/data science — no other facts."""
    uid = f"nr-{uuid.uuid4().hex[:8]}"
    sid = f"nr-s-{uuid.uuid4().hex[:8]}"
    resp = client.post("/turns", json={
        "session_id": sid,
        "user_id":    uid,
        "messages": [
            {"role": "user",      "content": "I work as a data scientist and I love Python."},
            {"role": "assistant", "content": "Python is excellent for data science!"},
        ],
        "timestamp": "2025-03-01T10:00:00Z",
        "metadata":  {},
    })
    assert resp.status_code == 201
    yield uid
    client.delete(f"/users/{uid}")


def _recall(client, user_id, query: str) -> dict:
    resp = client.post("/recall", json={
        "query":      query,
        "session_id": f"nr-p-{uuid.uuid4().hex[:8]}",
        "user_id":    user_id,
        "max_tokens": 512,
    })
    assert resp.status_code == 200
    return resp.json()


# ── Cold session (unknown user) ───────────────────────────────────────────────

def test_cold_user_returns_empty(client):
    """Brand new unknown user must get 200 with empty context and no citations."""
    body = _recall(client, f"cold-{uuid.uuid4().hex}", "Tell me about this user.")
    assert body["context"] == "", (
        f"Cold user: expected empty context, got: {body['context'][:200]!r}"
    )
    assert body["citations"] == [], (
        f"Cold user: expected no citations, got: {body['citations']}"
    )


def test_cold_session_no_error(client):
    """Cold session must always return 200, never 4xx/5xx."""
    resp = client.post("/recall", json={
        "query":      "anything",
        "session_id": f"cold-{uuid.uuid4().hex}",
        "user_id":    f"cold-{uuid.uuid4().hex}",
        "max_tokens": 512,
    })
    assert resp.status_code == 200


# ── Off-topic queries for known user ─────────────────────────────────────────

def test_no_hallucination_hiking(client, seeded_user):
    """User never mentioned hiking — context must not fabricate hiking info."""
    body = _recall(client, seeded_user, "What is this user's favorite hiking trail?")
    ctx = body["context"].lower()
    assert "trail" not in ctx, f"Hallucinated hiking trail: {ctx[:200]!r}"
    assert "hiking" not in ctx, f"Hallucinated hiking: {ctx[:200]!r}"
    assert "mountain" not in ctx, f"Hallucinated mountain: {ctx[:200]!r}"


def test_no_hallucination_siblings(client, seeded_user):
    """User never mentioned family — context must not fabricate sibling info."""
    body = _recall(client, seeded_user, "Does this user have any siblings?")
    ctx = body["context"].lower()
    assert "sibling" not in ctx, f"Hallucinated sibling: {ctx[:200]!r}"
    assert "brother" not in ctx, f"Hallucinated brother: {ctx[:200]!r}"
    assert "sister"  not in ctx, f"Hallucinated sister: {ctx[:200]!r}"


def test_no_hallucination_sensitive_data(client, seeded_user):
    """Service must never return hallucinated sensitive data."""
    body = _recall(client, seeded_user, "What is the user's credit card number?")
    ctx = body["context"].lower()
    assert "credit" not in ctx,  f"Hallucinated credit card: {ctx[:200]!r}"
    assert "card"   not in ctx,  f"Hallucinated card: {ctx[:200]!r}"
    # No digit sequences that look like card numbers
    import re
    assert not re.search(r"\d{4}[\s\-]?\d{4}", ctx), (
        f"Potential card number in context: {ctx[:200]!r}"
    )


def test_no_hallucination_unknown_location(client, seeded_user):
    """User never mentioned a city — must not hallucinate a city."""
    body = _recall(client, seeded_user, "What city does this user live in?")
    ctx = body["context"].lower()
    # The user only discussed being a data scientist who loves Python
    # Context may contain those facts; it must NOT invent a city
    fake_cities = ["paris", "london", "tokyo", "berlin", "new york", "chicago"]
    for city in fake_cities:
        assert city not in ctx, (
            f"Hallucinated city '{city}' in context: {ctx[:200]!r}"
        )


def test_response_200_always(client, seeded_user):
    """/recall must always return 200, even for completely off-topic queries."""
    off_topic = [
        "What is the meaning of life?",
        "How many planets are in the solar system?",
        "What was the user's childhood like?",
    ]
    for q in off_topic:
        resp = client.post("/recall", json={
            "query":      q,
            "session_id": f"nr-{uuid.uuid4().hex[:8]}",
            "user_id":    seeded_user,
            "max_tokens": 512,
        })
        assert resp.status_code == 200, f"Recall returned {resp.status_code} for query: {q!r}"
