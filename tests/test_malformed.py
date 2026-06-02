"""Malformed input tests — eval category #8: Robustness.

Service must:
  - Return 4xx on bad input, never 5xx crash
  - Stay up and serve subsequent valid requests after any bad input
  - Handle unicode/emoji/RTL gracefully (201)
  - Handle oversized payloads without crashing
"""
from __future__ import annotations

import pytest


def _health_ok(client) -> bool:
    try:
        return client.get("/health").json().get("status") == "ok"
    except Exception:
        return False


# ── /turns bad input ──────────────────────────────────────────────────────────

def test_missing_session_id(client):
    resp = client.post("/turns", json={
        "user_id":   "user-1",
        "messages":  [{"role": "user", "content": "hello"}],
        "timestamp": "2025-03-15T10:30:00Z",
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client), "Service crashed after malformed input"


def test_missing_messages(client):
    resp = client.post("/turns", json={
        "session_id": "s1",
        "user_id":    "user-1",
        "timestamp":  "2025-03-15T10:30:00Z",
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_empty_messages(client):
    resp = client.post("/turns", json={
        "session_id": "s1",
        "user_id":    "user-1",
        "messages":   [],
        "timestamp":  "2025-03-15T10:30:00Z",
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_messages_wrong_type(client):
    resp = client.post("/turns", json={
        "session_id": "s1",
        "user_id":    "user-1",
        "messages":   "not-an-array",
        "timestamp":  "2025-03-15T10:30:00Z",
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_bad_json_body(client):
    resp = client.post(
        "/turns",
        content=b"not-json{{{{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_invalid_timestamp(client):
    resp = client.post("/turns", json={
        "session_id": "s1",
        "user_id":    "user-1",
        "messages":   [{"role": "user", "content": "hi"}],
        "timestamp":  "not-a-date",
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_unicode_content_accepted(client, unique_user, unique_session):
    """Unicode, emoji, and RTL text must succeed with 201 — not 503."""
    resp = client.post("/turns", json={
        "session_id": unique_session,
        "user_id":    unique_user,
        "messages": [
            {"role": "user",      "content": "🐕 こんにちは！I love 東京 and Ünïcödé! مرحبا"},
            {"role": "assistant", "content": "✓ Stored safely. שלום"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata":  {},
    })
    # Unicode must be stored correctly — 201, not a crash
    assert resp.status_code == 201, (
        f"Unicode input rejected with {resp.status_code}: {resp.text[:200]}"
    )
    assert _health_ok(client)


def test_oversized_message_does_not_crash(client, unique_user, unique_session):
    """Very large content must be truncated/rejected gracefully — never 500."""
    huge = "x" * 50_000
    resp = client.post("/turns", json={
        "session_id": unique_session,
        "user_id":    unique_user,
        "messages": [
            {"role": "user",      "content": huge},
            {"role": "assistant", "content": "ok"},
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata":  {},
    })
    # 201 (truncated) or 413 (rejected) — never 500 crash
    assert resp.status_code in (201, 413), (
        f"Expected 201 or 413 for oversized input, got {resp.status_code}"
    )
    assert _health_ok(client)


# ── /recall bad input ─────────────────────────────────────────────────────────

def test_recall_missing_query(client):
    resp = client.post("/recall", json={
        "session_id": "s1",
        "max_tokens": 512,
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_recall_empty_query(client):
    resp = client.post("/recall", json={
        "query":      "",
        "session_id": "s1",
        "max_tokens": 512,
    })
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


# ── /search bad input ─────────────────────────────────────────────────────────

def test_search_missing_query(client):
    resp = client.post("/search", json={"limit": 5})
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


def test_search_empty_query(client):
    resp = client.post("/search", json={"query": "", "limit": 5})
    assert resp.status_code in (400, 422)
    assert _health_ok(client)


# ── Service stays up after a sequence of bad requests ────────────────────────

def test_service_alive_after_barrage(client):
    """Send 5 bad requests in a row; service must still respond to /health."""
    bad_payloads = [
        {},
        {"session_id": "x"},
        {"session_id": "x", "messages": None},
        {"session_id": "", "messages": [], "timestamp": "bad"},
        b"garbage",
    ]
    for payload in bad_payloads:
        if isinstance(payload, bytes):
            client.post("/turns", content=payload,
                        headers={"Content-Type": "application/json"})
        else:
            client.post("/turns", json=payload)

    assert _health_ok(client), "Service is down after bad-request barrage"
