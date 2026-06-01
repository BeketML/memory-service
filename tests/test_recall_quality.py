"""Recall quality tests using the fixture conversations + probe queries."""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest

FIXTURES_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "recall_quality.json"


def load_fixtures():
    with open(FIXTURES_PATH) as f:
        return json.load(f)


def test_recall_quality_fixture(client):
    fixtures = load_fixtures()
    total_probes = 0
    passed_probes = 0
    results_log = []

    for scenario in fixtures["scenarios"]:
        user_id = f"qa-{uuid.uuid4().hex[:8]}"
        sessions = {}

        try:
            # Ingest all turns for this scenario
            for turn in scenario["turns"]:
                session_id = sessions.setdefault(turn["session"], f"qa-{uuid.uuid4().hex[:8]}")
                resp = client.post(
                    "/turns",
                    json={
                        "session_id": session_id,
                        "user_id": user_id,
                        "messages": turn["messages"],
                        "timestamp": turn["timestamp"],
                        "metadata": {},
                    },
                )
                assert resp.status_code == 201, f"Turn ingest failed: {resp.text}"

            # Run probe queries
            probe_session = f"probe-{uuid.uuid4().hex[:8]}"
            for probe in scenario["probes"]:
                total_probes += 1
                resp = client.post(
                    "/recall",
                    json={
                        "query": probe["query"],
                        "session_id": probe_session,
                        "user_id": user_id,
                        "max_tokens": 1024,
                    },
                )
                assert resp.status_code == 200
                context = resp.json()["context"].lower()
                expected_facts = probe["expected_facts"]

                matched = [f for f in expected_facts if f.lower() in context]
                score = len(matched) / len(expected_facts) if expected_facts else 1.0

                result = {
                    "scenario": scenario["name"],
                    "query": probe["query"],
                    "expected": expected_facts,
                    "matched": matched,
                    "score": score,
                    "context_snippet": context[:300],
                }
                results_log.append(result)

                if score >= 0.5:
                    passed_probes += 1

        finally:
            client.delete(f"/users/{user_id}")

    overall_score = passed_probes / total_probes if total_probes else 0.0

    # Print detailed results
    print(f"\n=== Recall Quality Results ===")
    print(f"Probes passed: {passed_probes}/{total_probes} ({overall_score:.1%})")
    for r in results_log:
        status = "✓" if r["score"] >= 0.5 else "✗"
        print(f"  {status} [{r['scenario']}] {r['query']!r}")
        print(f"    Expected: {r['expected']}")
        print(f"    Matched:  {r['matched']}")
        if r["score"] < 0.5:
            print(f"    Context: {r['context_snippet']!r}")

    assert overall_score >= 0.5, (
        f"Recall quality too low: {passed_probes}/{total_probes} ({overall_score:.1%}). "
        "Check extraction and retrieval pipeline."
    )


def test_supersession_visible_in_recall(client):
    user_id = f"super-{uuid.uuid4().hex[:8]}"
    sess1 = f"sess1-{uuid.uuid4().hex[:8]}"
    sess2 = f"sess2-{uuid.uuid4().hex[:8]}"

    try:
        # Session 1: user works at Stripe
        client.post(
            "/turns",
            json={
                "session_id": sess1,
                "user_id": user_id,
                "messages": [
                    {"role": "user", "content": "I work at Stripe as a backend engineer."},
                    {"role": "assistant", "content": "Great company!"},
                ],
                "timestamp": "2025-03-01T10:00:00Z",
                "metadata": {},
            },
        )

        # Session 2: user switches to Notion
        client.post(
            "/turns",
            json={
                "session_id": sess2,
                "user_id": user_id,
                "messages": [
                    {"role": "user", "content": "I just started at Notion as a PM."},
                    {"role": "assistant", "content": "Congratulations!"},
                ],
                "timestamp": "2025-03-15T10:00:00Z",
                "metadata": {},
            },
        )

        # Recall should show current employer (Notion), not Stripe
        resp = client.post(
            "/recall",
            json={
                "query": "Where does this user work?",
                "session_id": f"probe-{uuid.uuid4().hex[:8]}",
                "user_id": user_id,
                "max_tokens": 512,
            },
        )
        assert resp.status_code == 200
        context = resp.json()["context"].lower()
        assert "notion" in context, f"Expected Notion in recall, got: {context!r}"

        # Memories should show supersession chain
        resp2 = client.get(f"/users/{user_id}/memories")
        mems = resp2.json()["memories"]
        employer_mems = [m for m in mems if "employer" in m["key"].lower()]

        active = [m for m in employer_mems if m["active"]]
        inactive = [m for m in employer_mems if not m["active"]]

        assert len(active) <= 1, "Should have at most one active employer"
        if active:
            assert "notion" in active[0]["value"].lower(), (
                f"Active employer should be Notion: {active[0]['value']}"
            )

    finally:
        client.delete(f"/users/{user_id}")
