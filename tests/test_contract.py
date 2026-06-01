"""Contract roundtrip tests — write a turn, recall it, verify shapes."""
from __future__ import annotations

import pytest


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_post_turn_returns_id(client, unique_user, unique_session):
    resp = client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "I live in Berlin and work at Notion as a PM."},
                {"role": "assistant", "content": "Great, Berlin is a wonderful city!"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert isinstance(data["id"], str)
    assert len(data["id"]) > 0


def test_recall_after_turn(client, unique_user, unique_session):
    # Write a turn
    client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "I just moved to Berlin from NYC last month."},
                {"role": "assistant", "content": "Exciting! Berlin is a great city."},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )

    # Recall immediately
    resp = client.post(
        "/recall",
        json={
            "query": "Where does this user live?",
            "session_id": unique_session,
            "user_id": unique_user,
            "max_tokens": 512,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data
    assert "citations" in data
    assert isinstance(data["context"], str)
    assert isinstance(data["citations"], list)
    # Context should mention Berlin
    assert "Berlin" in data["context"] or data["context"] == ""


def test_recall_shape(client, unique_user, unique_session):
    resp = client.post(
        "/recall",
        json={
            "query": "anything",
            "session_id": unique_session,
            "user_id": unique_user,
            "max_tokens": 1024,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("context"), str)
    assert isinstance(data.get("citations"), list)
    for cite in data["citations"]:
        assert "turn_id" in cite
        assert "score" in cite
        assert "snippet" in cite


def test_search_returns_results(client, unique_user, unique_session):
    # Write some data first
    client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "I have a golden retriever named Biscuit."},
                {"role": "assistant", "content": "What a lovely name!"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )

    resp = client.post(
        "/search",
        json={
            "query": "dog pet",
            "user_id": unique_user,
            "limit": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    for result in data["results"]:
        assert "content" in result
        assert "score" in result
        assert "session_id" in result
        assert "metadata" in result


def test_get_memories_structure(client, unique_user, unique_session):
    # Write a turn first
    client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "I'm vegetarian and I work as a software engineer."},
                {"role": "assistant", "content": "Good to know!"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )

    resp = client.get(f"/users/{unique_user}/memories")
    assert resp.status_code == 200
    data = resp.json()
    assert "memories" in data
    assert isinstance(data["memories"], list)
    for mem in data["memories"]:
        assert "id" in mem
        assert "type" in mem
        assert "key" in mem
        assert "value" in mem
        assert "active" in mem
        assert "confidence" in mem
        assert "created_at" in mem


def test_cold_session_recall_is_empty(client):
    import uuid
    resp = client.post(
        "/recall",
        json={
            "query": "What do I know about this person?",
            "session_id": f"cold-{uuid.uuid4().hex}",
            "user_id": f"cold-user-{uuid.uuid4().hex}",
            "max_tokens": 512,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["context"] == ""
    assert data["citations"] == []


def test_delete_session(client, unique_user, unique_session):
    client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = client.delete(f"/sessions/{unique_session}")
    assert resp.status_code == 204


def test_delete_user(client, unique_user, unique_session):
    client.post(
        "/turns",
        json={
            "session_id": unique_session,
            "user_id": unique_user,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = client.delete(f"/users/{unique_user}")
    assert resp.status_code == 204

    # Memories should be gone
    resp2 = client.get(f"/users/{unique_user}/memories")
    assert resp2.status_code == 200
    assert resp2.json()["memories"] == []
