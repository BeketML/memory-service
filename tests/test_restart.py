"""Restart persistence test — task.md §7.

Verifies that data written before a service restart is still queryable after.

Running this test requires Docker access from the test environment.
It is skipped automatically if the `docker` command is unavailable or
the SKIP_RESTART_TEST=1 environment variable is set.

Usage:
    # Full test (restarts the app container — ~30s):
    pytest tests/test_restart.py -v -s

    # Skip restart (CI without Docker socket):
    SKIP_RESTART_TEST=1 pytest tests/test_restart.py -v
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest
import httpx

BASE_URL = "http://localhost:8080"
TIMEOUT = 90.0


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "app"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


skip_if_no_docker = pytest.mark.skipif(
    os.environ.get("SKIP_RESTART_TEST") == "1" or not _docker_available(),
    reason="Restart test requires Docker and SKIP_RESTART_TEST != 1",
)


def _wait_healthy(client: httpx.Client, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = client.get("/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


@skip_if_no_docker
def test_data_survives_restart():
    """Write a turn, restart the app container, verify recall still works."""
    user_id = f"restart-test-{uuid.uuid4().hex[:8]}"
    session_id = f"restart-sess-{uuid.uuid4().hex[:8]}"

    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        # 1. Write a turn with a distinctive fact
        resp = client.post("/turns", json={
            "session_id": session_id,
            "user_id": user_id,
            "messages": [
                {"role": "user",
                 "content": "I am a robotics engineer at SpaceX in Hawthorne."},
                {"role": "assistant",
                 "content": "That's exciting! Rocket science is fascinating."},
            ],
            "timestamp": "2025-06-01T10:00:00Z",
            "metadata": {},
        })
        assert resp.status_code == 201, f"POST /turns failed: {resp.text}"
        turn_id = resp.json()["id"]

        # 2. Verify recall works before restart
        recall_pre = client.post("/recall", json={
            "query": "Where does this user work?",
            "session_id": session_id,
            "user_id": user_id,
            "max_tokens": 512,
        })
        assert recall_pre.status_code == 200
        pre_context = recall_pre.json()["context"].lower()
        assert "spacex" in pre_context or "hawthorne" in pre_context or "robotics" in pre_context, (
            f"Pre-restart recall missing expected fact: {recall_pre.json()['context'][:200]}"
        )

    # 3. Restart the app container (leaves pgdata volume untouched)
    result = subprocess.run(
        ["docker", "compose", "restart", "app"],
        capture_output=True, timeout=60,
    )
    assert result.returncode == 0, f"docker compose restart failed: {result.stderr.decode()}"

    # 4. Wait for the service to come back healthy
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        healthy = _wait_healthy(client, timeout=60.0)
        assert healthy, "Service did not become healthy after restart within 60s"

        # 5. Verify the same data is still queryable
        recall_post = client.post("/recall", json={
            "query": "Where does this user work?",
            "session_id": session_id,
            "user_id": user_id,
            "max_tokens": 512,
        })
        assert recall_post.status_code == 200
        post_context = recall_post.json()["context"].lower()
        assert "spacex" in post_context or "hawthorne" in post_context or "robotics" in post_context, (
            f"Post-restart recall lost data: {recall_post.json()['context'][:200]}"
        )

        # 6. The turn id from before restart is still reachable
        memories = client.get(f"/users/{user_id}/memories")
        assert memories.status_code == 200
        mem_list = memories.json()["memories"]
        assert any(
            str(m.get("source_turn")) == turn_id
            for m in mem_list
        ), "Memory provenance (source_turn) lost after restart"

        # Cleanup
        client.delete(f"/users/{user_id}")
