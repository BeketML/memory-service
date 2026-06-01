"""Malformed input tests — 4xx, never crash."""
from __future__ import annotations

import pytest


def test_missing_session_id(client):
    resp = client.post(
        "/turns",
        json={
            "user_id": "user-1",
            "messages": [{"role": "user", "content": "hello"}],
            "timestamp": "2025-03-15T10:30:00Z",
        },
    )
    assert resp.status_code == 400


def test_empty_messages(client):
    resp = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "user-1",
            "messages": [],
            "timestamp": "2025-03-15T10:30:00Z",
        },
    )
    assert resp.status_code == 400


def test_bad_json_turns(client):
    resp = client.post(
        "/turns",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_missing_query_recall(client):
    resp = client.post(
        "/recall",
        json={"session_id": "s1", "max_tokens": 512},
    )
    assert resp.status_code == 422


def test_empty_query_recall(client):
    resp = client.post(
        "/recall",
        json={"query": "", "session_id": "s1", "max_tokens": 512},
    )
    assert resp.status_code == 400


def test_missing_query_search(client):
    resp = client.post("/search", json={"limit": 5})
    assert resp.status_code == 422


def test_unicode_content(client, unique_user, unique_session):
    resp = client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "こんにちは！私はベルリンに住んでいます 🐕 emoji 😀"},
                {"role": "assistant", "content": "素晴らしい！"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    # Should not crash — 201 or 503 (LLM issue), but not 500 crash
    assert resp.status_code in (201, 503)


def test_oversized_message(client, unique_user, unique_session):
    huge_content = "x" * 50000
    resp = client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": huge_content},
                {"role": "assistant", "content": "ok"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    assert resp.status_code in (201, 413, 503)


def test_invalid_timestamp(client):
    resp = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "user-1",
            "messages": [{"role": "user", "content": "hi"}],
            "timestamp": "not-a-date",
        },
    )
    assert resp.status_code in (400, 422)
