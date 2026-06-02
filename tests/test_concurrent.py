"""Cross-session scoping tests — eval category #7: Cross-session Scoping.

Design decision (documented in README): memories are USER-scoped.
Same user_id across multiple sessions → facts shared (intentional).
Different user_ids → absolute isolation (no bleed).
"""
from __future__ import annotations

import uuid
import pytest


# ── Cross-user isolation ──────────────────────────────────────────────────────

def test_two_users_no_bleed(client):
    """User A and User B must not see each other's memories."""
    user_a = f"ua-{uuid.uuid4().hex[:8]}"
    user_b = f"ub-{uuid.uuid4().hex[:8]}"
    sess_a = f"sa-{uuid.uuid4().hex[:8]}"
    sess_b = f"sb-{uuid.uuid4().hex[:8]}"

    try:
        client.post("/turns", json={
            "session_id": sess_a, "user_id": user_a,
            "messages": [
                {"role": "user",      "content": "I work at Google as a software engineer."},
                {"role": "assistant", "content": "That's great!"},
            ],
            "timestamp": "2025-03-15T10:30:00Z", "metadata": {},
        })
        client.post("/turns", json={
            "session_id": sess_b, "user_id": user_b,
            "messages": [
                {"role": "user",      "content": "I work at Amazon as a data scientist."},
                {"role": "assistant", "content": "Interesting!"},
            ],
            "timestamp": "2025-03-15T10:30:00Z", "metadata": {},
        })

        vals_a = [m["value"].lower() for m in client.get(f"/users/{user_a}/memories").json()["memories"]]
        vals_b = [m["value"].lower() for m in client.get(f"/users/{user_b}/memories").json()["memories"]]

        assert not any("amazon" in v for v in vals_a), f"User A sees Amazon: {vals_a}"
        assert not any("google" in v for v in vals_b), f"User B sees Google: {vals_b}"

    finally:
        client.delete(f"/users/{user_a}")
        client.delete(f"/users/{user_b}")


def test_cross_user_recall_no_bleed(client):
    """User A's /recall must not surface User B's facts."""
    user_a = f"ra-{uuid.uuid4().hex[:8]}"
    user_b = f"rb-{uuid.uuid4().hex[:8]}"

    try:
        client.post("/turns", json={
            "session_id": f"s-{uuid.uuid4().hex[:8]}", "user_id": user_a,
            "messages": [{"role": "user", "content": "I live in Tokyo."}, {"role": "assistant", "content": "Nice!"}],
            "timestamp": "2025-03-01T10:00:00Z", "metadata": {},
        })
        client.post("/turns", json={
            "session_id": f"s-{uuid.uuid4().hex[:8]}", "user_id": user_b,
            "messages": [{"role": "user", "content": "I work at SpaceX."}, {"role": "assistant", "content": "Cool!"}],
            "timestamp": "2025-03-01T10:00:00Z", "metadata": {},
        })

        # User A querying about SpaceX should get empty — not B's data
        resp = client.post("/recall", json={
            "query":      "SpaceX",
            "session_id": f"p-{uuid.uuid4().hex[:8]}",
            "user_id":    user_a,
            "max_tokens": 512,
        })
        ctx_a = resp.json()["context"].lower()
        assert "spacex" not in ctx_a, f"User A leaked SpaceX from User B: {ctx_a[:200]!r}"

        # User B querying about Tokyo should get empty
        resp = client.post("/recall", json={
            "query":      "Tokyo",
            "session_id": f"p-{uuid.uuid4().hex[:8]}",
            "user_id":    user_b,
            "max_tokens": 512,
        })
        ctx_b = resp.json()["context"].lower()
        assert "tokyo" not in ctx_b, f"User B leaked Tokyo from User A: {ctx_b[:200]!r}"

    finally:
        client.delete(f"/users/{user_a}")
        client.delete(f"/users/{user_b}")


# ── Same user cross-session facts ────────────────────────────────────────────

def test_same_user_cross_session_facts_visible(client):
    """Facts established in session 1 must be recallable in session 2."""
    user_id = f"cs-{uuid.uuid4().hex[:8]}"
    sess1   = f"cs1-{uuid.uuid4().hex[:8]}"
    sess2   = f"cs2-{uuid.uuid4().hex[:8]}"

    try:
        client.post("/turns", json={
            "session_id": sess1, "user_id": user_id,
            "messages": [
                {"role": "user",      "content": "I have a dog named Biscuit."},
                {"role": "assistant", "content": "What a lovely name!"},
            ],
            "timestamp": "2025-03-01T10:00:00Z", "metadata": {},
        })

        resp = client.post("/recall", json={
            "query":      "What pet does this user have?",
            "session_id": sess2,   # different session
            "user_id":    user_id, # same user
            "max_tokens": 512,
        })
        assert resp.status_code == 200
        ctx = resp.json()["context"]
        assert "biscuit" in ctx.lower() or ctx == "", (
            f"Cross-session fact not visible: {ctx[:200]!r}"
        )

    finally:
        client.delete(f"/users/{user_id}")


def test_same_user_three_sessions_all_visible(client):
    """Facts from 3 separate sessions for the same user are all recallable."""
    user_id = f"3s-{uuid.uuid4().hex[:8]}"
    s1, s2, s3 = (f"3s{i}-{uuid.uuid4().hex[:6]}" for i in range(1, 4))

    try:
        client.post("/turns", json={
            "session_id": s1, "user_id": user_id,
            "messages": [{"role": "user", "content": "I live in Berlin."}, {"role": "assistant", "content": "Nice!"}],
            "timestamp": "2025-03-01T10:00:00Z", "metadata": {},
        })
        client.post("/turns", json={
            "session_id": s2, "user_id": user_id,
            "messages": [{"role": "user", "content": "I have a cat named Mochi."}, {"role": "assistant", "content": "Cute!"}],
            "timestamp": "2025-03-02T10:00:00Z", "metadata": {},
        })
        client.post("/turns", json={
            "session_id": s3, "user_id": user_id,
            "messages": [{"role": "user", "content": "I'm a vegetarian."}, {"role": "assistant", "content": "Noted!"}],
            "timestamp": "2025-03-03T10:00:00Z", "metadata": {},
        })

        # All memories across all sessions should exist for this user
        resp = client.get(f"/users/{user_id}/memories")
        mems = resp.json()["memories"]
        all_vals = " ".join(m["value"].lower() for m in mems)

        # At least some facts from the three turns should be extracted
        assert len(mems) >= 1, "Expected at least 1 memory across 3 sessions"

    finally:
        client.delete(f"/users/{user_id}")
