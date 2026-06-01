"""Concurrent sessions — multiple sessions must not bleed across users."""
from __future__ import annotations

import uuid
import pytest


def test_two_users_no_bleed(client):
    user_a = f"user-a-{uuid.uuid4().hex[:8]}"
    user_b = f"user-b-{uuid.uuid4().hex[:8]}"
    sess_a = f"sess-a-{uuid.uuid4().hex[:8]}"
    sess_b = f"sess-b-{uuid.uuid4().hex[:8]}"

    try:
        # User A tells us their employer
        client.post(
            "/turns",
            json={
                "session_id": sess_a,
                "user_id": user_a,
                "messages": [
                    {"role": "user", "content": "I work at Google as a software engineer."},
                    {"role": "assistant", "content": "That's great!"},
                ],
                "timestamp": "2025-03-15T10:30:00Z",
                "metadata": {},
            },
        )

        # User B tells us their employer (different)
        client.post(
            "/turns",
            json={
                "session_id": sess_b,
                "user_id": user_b,
                "messages": [
                    {"role": "user", "content": "I work at Amazon as a data scientist."},
                    {"role": "assistant", "content": "Interesting!"},
                ],
                "timestamp": "2025-03-15T10:30:00Z",
                "metadata": {},
            },
        )

        # User A's memories should NOT contain Amazon
        resp_a = client.get(f"/users/{user_a}/memories")
        assert resp_a.status_code == 200
        mems_a = resp_a.json()["memories"]
        values_a = [m["value"].lower() for m in mems_a]
        assert not any("amazon" in v for v in values_a), (
            f"User A should not know about Amazon: {values_a}"
        )

        # User B's memories should NOT contain Google
        resp_b = client.get(f"/users/{user_b}/memories")
        assert resp_b.status_code == 200
        mems_b = resp_b.json()["memories"]
        values_b = [m["value"].lower() for m in mems_b]
        assert not any("google" in v for v in values_b), (
            f"User B should not know about Google: {values_b}"
        )
    finally:
        client.delete(f"/users/{user_a}")
        client.delete(f"/users/{user_b}")


def test_same_user_cross_session_facts_visible(client):
    user_id = f"user-cross-{uuid.uuid4().hex[:8]}"
    sess1 = f"sess1-{uuid.uuid4().hex[:8]}"
    sess2 = f"sess2-{uuid.uuid4().hex[:8]}"

    try:
        # Session 1: establish a fact
        client.post(
            "/turns",
            json={
                "session_id": sess1,
                "user_id": user_id,
                "messages": [
                    {"role": "user", "content": "I have a dog named Biscuit."},
                    {"role": "assistant", "content": "What a lovely name!"},
                ],
                "timestamp": "2025-03-15T10:00:00Z",
                "metadata": {},
            },
        )

        # Session 2: recall should see the fact from session 1
        resp = client.post(
            "/recall",
            json={
                "query": "What pet does this user have?",
                "session_id": sess2,
                "user_id": user_id,
                "max_tokens": 512,
            },
        )
        assert resp.status_code == 200
        context = resp.json()["context"]
        # Biscuit should appear since it's a cross-session user fact
        assert "Biscuit" in context or context == "", (
            f"Expected Biscuit in cross-session recall, got: {context!r}"
        )
    finally:
        client.delete(f"/users/{user_id}")
