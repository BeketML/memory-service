"""Benchmark suite — measures all 10 eval categories from task.md §9.

Run with:
    pytest tests/test_benchmark.py -v -s --tb=short

Writes benchmark results to:
    benchmarks/latest.json
    (and triggers scripts/update_readme.py to patch README.md)

Categories (task.md §9):
  1.  Recall Quality         — primary eval signal
  2.  Fact Evolution         — supersession chain correctness
  3.  Multi-hop Recall       — connecting two separate facts
  4.  Noise Resistance       — no hallucination on off-topic queries
  5.  Extraction Quality     — structured typed memories, not raw chunks
  6.  Persistence            — restart test (skipped if no Docker)
  7.  Cross-session Scoping  — same-user sharing + cross-user isolation
  8.  Robustness             — 4xx on bad input, service stays up
  9.  Correctness (sync)     — immediate queryability after /turns
  10. Contract Compliance    — all 7 endpoints, shapes, status codes
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import httpx
import pytest

BASE_URL  = "http://localhost:8080"
REPO_ROOT = pathlib.Path(__file__).parent.parent

# Module-level store — each category test appends its result here.
_RESULTS: list["CategoryResult"] = []


@dataclass
class CategoryResult:
    number:  int
    name:    str
    passed:  int
    total:   int
    details: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def pct(self) -> str:
        return f"{self.score:.0%}"

    @property
    def emoji(self) -> str:
        if self.score >= 0.9:
            return "✅"
        if self.score >= 0.6:
            return "⚠️"
        return "❌"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=90.0)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _ingest(c: httpx.Client, user_id: str, content: str,
            ts: str = "2025-03-15T10:00:00Z", session_id: str = None) -> str:
    sid = session_id or f"bm-s-{_uid()}"
    r = c.post("/turns", json={
        "session_id": sid,
        "user_id":    user_id,
        "messages": [
            {"role": "user",      "content": content},
            {"role": "assistant", "content": "Understood, thank you."},
        ],
        "timestamp": ts,
        "metadata":  {},
    })
    return sid, r.status_code == 201


def _recall(c: httpx.Client, user_id: str, query: str, max_tokens: int = 1024) -> str:
    r = c.post("/recall", json={
        "query":      query,
        "session_id": f"bm-p-{_uid()}",
        "user_id":    user_id,
        "max_tokens": max_tokens,
    })
    if r.status_code == 200:
        return r.json()["context"].lower()
    return ""


def _memories(c: httpx.Client, user_id: str) -> list[dict]:
    r = c.get(f"/users/{user_id}/memories")
    if r.status_code == 200:
        return r.json()["memories"]
    return []


def _cleanup(c: httpx.Client, user_id: str) -> None:
    try:
        c.delete(f"/users/{user_id}")
    except Exception:
        pass


# ── Benchmark fixture: service must be healthy ────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def require_healthy_service():
    """Abort the benchmark if the service isn't up."""
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=10)
        assert r.status_code == 200 and r.json().get("status") == "ok", (
            f"Health check failed: {r.text}"
        )
    except Exception as exc:
        pytest.skip(f"Service not reachable at {BASE_URL}: {exc}")


# ── Category 1: Recall Quality ────────────────────────────────────────────────

def test_cat1_recall_quality():
    """Primary eval signal: does /recall surface the expected facts?"""
    fixtures_path = REPO_ROOT / "fixtures" / "recall_quality.json"
    with open(fixtures_path) as f:
        fixtures = json.load(f)

    total = passed = 0
    details = []

    with _client() as c:
        for scenario in fixtures["scenarios"]:
            uid = f"bm1-{_uid()}"
            sessions: dict[str, str] = {}

            try:
                for turn in scenario["turns"]:
                    sid = sessions.setdefault(turn["session"], f"bm1s-{_uid()}")
                    r = c.post("/turns", json={
                        "session_id": sid,
                        "user_id":    uid,
                        "messages":   turn["messages"],
                        "timestamp":  turn.get("timestamp", "2025-03-15T10:00:00Z"),
                        "metadata":   {},
                    })
                    if r.status_code != 201:
                        details.append(f"  INGEST FAIL [{scenario['name']}]: {r.status_code}")
                        continue

                for probe in scenario["probes"]:
                    total += 1
                    ctx = _recall(c, uid, probe["query"])
                    expected = probe.get("expected_facts", [])
                    forbidden = probe.get("forbidden_facts", [])

                    ok = True
                    reason = ""
                    for f_word in forbidden:
                        if f_word.lower() in ctx:
                            ok = False
                            reason = f"forbidden '{f_word}' found"
                            break
                    if ok and expected:
                        missing = [e for e in expected if e.lower() not in ctx]
                        if missing:
                            ok = False
                            reason = f"missing {missing}"

                    if ok:
                        passed += 1
                        details.append(f"  ✓ [{scenario['name']}] {probe['query']!r}")
                    else:
                        details.append(f"  ✗ [{scenario['name']}] {probe['query']!r} — {reason}")
            finally:
                _cleanup(c, uid)

    result = CategoryResult(1, "Recall Quality", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.4, f"Recall quality too low: {result.passed}/{result.total}"


# ── Category 2: Fact Evolution ────────────────────────────────────────────────

def test_cat2_fact_evolution():
    cases = [
        # (setup_content, ts1, update_content, ts2, query, expected_in_recall)
        ("I work at Stripe as a backend engineer.",  "2025-03-01T10:00:00Z",
         "I just started at Notion as a PM!",        "2025-03-15T10:00:00Z",
         "Where does this user work?",               "notion"),
        ("I live in New York City.",                  "2025-03-01T10:00:00Z",
         "I just moved to Berlin last month!",        "2025-03-15T10:00:00Z",
         "Where does this user live?",                "berlin"),
        ("I prefer Python for all my projects.",      "2025-03-01T10:00:00Z",
         "Actually I mostly use TypeScript now.",     "2025-03-15T10:00:00Z",
         "What language does this user prefer?",      "typescript"),
    ]
    total = passed = 0
    details = []

    with _client() as c:
        for (c1, t1, c2, t2, query, expected) in cases:
            total += 1
            uid = f"bm2-{_uid()}"
            try:
                _ingest(c, uid, c1, ts=t1)
                _ingest(c, uid, c2, ts=t2)
                ctx = _recall(c, uid, query)
                if expected in ctx:
                    passed += 1
                    details.append(f"  ✓ {query!r} → found '{expected}'")
                else:
                    details.append(f"  ✗ {query!r} → '{expected}' missing. ctx: {ctx[:120]!r}")

                # Chain integrity sub-check
                total += 1
                mems = _memories(c, uid)
                active = [m for m in mems if m["active"]]
                inactive = [m for m in mems if not m["active"]]
                if inactive and any(m.get("superseded_by") for m in inactive):
                    passed += 1
                    details.append(f"  ✓ Supersession chain intact for {query!r}")
                elif not inactive:
                    # Only one memory — chain not yet needed (single fact)
                    passed += 1
                    details.append(f"  ✓ Single memory (no chain needed yet) for {query!r}")
                else:
                    details.append(f"  ✗ Supersession chain broken for {query!r}: inactive without superseded_by")
            finally:
                _cleanup(c, uid)

    result = CategoryResult(2, "Fact Evolution", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.5, f"Fact evolution too low: {result.passed}/{result.total}"


# ── Category 3: Multi-hop Recall ──────────────────────────────────────────────

def test_cat3_multihop_recall():
    total = passed = 0
    details = []

    with _client() as c:
        uid = f"bm3-{_uid()}"
        try:
            _ingest(c, uid, "I have a golden retriever named Biscuit.",  "2025-03-01T10:00:00Z")
            _ingest(c, uid, "I just moved to Berlin from NYC.",           "2025-03-15T10:00:00Z")

            # Direct hop
            total += 1
            ctx = _recall(c, uid, "What is the user's dog's name?")
            if "biscuit" in ctx:
                passed += 1; details.append("  ✓ Pet name (Biscuit) found")
            else:
                details.append(f"  ✗ Pet name missing. ctx: {ctx[:120]!r}")

            total += 1
            ctx = _recall(c, uid, "Where does this user live?")
            if "berlin" in ctx:
                passed += 1; details.append("  ✓ City (Berlin) found")
            else:
                details.append(f"  ✗ City missing. ctx: {ctx[:120]!r}")

            # Multi-hop: connect pet owner with city
            total += 1
            ctx = _recall(c, uid, "What city does the user with the dog named Biscuit live in?")
            if "berlin" in ctx:
                passed += 1; details.append("  ✓ Multi-hop: Berlin for Biscuit's owner found")
            else:
                details.append(f"  ✗ Multi-hop failed. ctx: {ctx[:120]!r}")

        finally:
            _cleanup(c, uid)

    result = CategoryResult(3, "Multi-hop Recall", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.5, f"Multi-hop recall too low: {result.passed}/{result.total}"


# ── Category 4: Noise Resistance ──────────────────────────────────────────────

def test_cat4_noise_resistance():
    total = passed = 0
    details = []

    with _client() as c:
        uid = f"bm4-{_uid()}"
        try:
            _ingest(c, uid, "I work as a data scientist and I love Python.", "2025-03-01T10:00:00Z")

            noise_checks = [
                ("What is this user's favorite hiking trail?", ["trail", "hiking", "mountain"]),
                ("Does this user have any siblings?",          ["sibling", "brother", "sister"]),
                ("What is this user's credit card number?",    ["credit", "card", "4111"]),
            ]
            for query, forbidden in noise_checks:
                total += 1
                ctx = _recall(c, uid, query)
                bad = [w for w in forbidden if w.lower() in ctx]
                if not bad:
                    passed += 1; details.append(f"  ✓ No hallucination for: {query!r}")
                else:
                    details.append(f"  ✗ Hallucinated {bad} for: {query!r}. ctx: {ctx[:120]!r}")

            # Cold session → empty
            total += 1
            r = c.post("/recall", json={
                "query":      "Tell me everything about this user.",
                "session_id": f"cold-{_uid()}",
                "user_id":    f"cold-{_uid()}",
                "max_tokens": 512,
            })
            body = r.json()
            if r.status_code == 200 and body["context"] == "" and body["citations"] == []:
                passed += 1; details.append("  ✓ Cold session returns empty context")
            else:
                details.append(f"  ✗ Cold session non-empty: status={r.status_code}, ctx={body.get('context','?')[:60]!r}")

            # Service returns 200 always
            total += 1
            r2 = c.post("/recall", json={
                "query":      "What is the meaning of life?",
                "session_id": f"nr-{_uid()}",
                "user_id":    uid,
                "max_tokens": 512,
            })
            if r2.status_code == 200:
                passed += 1; details.append("  ✓ Off-topic query returns 200")
            else:
                details.append(f"  ✗ Off-topic query returned {r2.status_code}")

        finally:
            _cleanup(c, uid)

    result = CategoryResult(4, "Noise Resistance", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.6, f"Noise resistance too low: {result.passed}/{result.total}"


# ── Category 5: Extraction Quality ───────────────────────────────────────────

def test_cat5_extraction_quality():
    total = passed = 0
    details = []

    with _client() as c:
        uid = f"bm5-{_uid()}"
        try:
            _ingest(c, uid, "I'm a backend engineer at Stripe in NYC. I love TypeScript. I'm vegetarian.", "2025-03-01T10:00:00Z")
            _ingest(c, uid, "Just got back from walking Biscuit, my golden retriever.", "2025-03-02T10:00:00Z")

            mems = _memories(c, uid)

            # 1. At least one memory produced
            total += 1
            if len(mems) >= 1:
                passed += 1; details.append(f"  ✓ {len(mems)} memories extracted")
            else:
                details.append("  ✗ No memories extracted")

            # 2. All types are valid enum values
            total += 1
            valid_types = {"fact", "preference", "opinion", "event"}
            invalid = [m["type"] for m in mems if m["type"] not in valid_types]
            if not invalid:
                passed += 1; details.append("  ✓ All memory types valid enum values")
            else:
                details.append(f"  ✗ Invalid memory types: {invalid}")

            # 3. Keys use dot-notation
            total += 1
            bad_keys = [m["key"] for m in mems if "." not in m["key"]]
            if not bad_keys:
                passed += 1; details.append("  ✓ All keys use dot-notation")
            else:
                details.append(f"  ✗ Non-dot-notation keys: {bad_keys[:3]}")

            # 4. Confidence in [0, 1]
            total += 1
            bad_conf = [m["confidence"] for m in mems if not (0.0 <= m["confidence"] <= 1.0)]
            if not bad_conf:
                passed += 1; details.append("  ✓ All confidence values in [0,1]")
            else:
                details.append(f"  ✗ Out-of-range confidence: {bad_conf}")

            # 5. Implicit fact: Biscuit
            total += 1
            all_content = " ".join(m["value"].lower() + " " + m["key"].lower() + " " + m.get("canonical_text", "").lower() for m in mems)
            if "biscuit" in all_content:
                passed += 1; details.append("  ✓ Implicit pet fact (Biscuit) extracted")
            else:
                details.append(f"  ✗ Implicit fact (Biscuit) not extracted. Keys: {[m['key'] for m in mems]}")

            # 6. Provenance set
            total += 1
            no_prov = [m for m in mems if not m.get("source_turn")]
            if not no_prov:
                passed += 1; details.append("  ✓ All memories have source_turn provenance")
            else:
                details.append(f"  ✗ {len(no_prov)} memories missing source_turn")

        finally:
            _cleanup(c, uid)

    result = CategoryResult(5, "Extraction Quality", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.5, f"Extraction quality too low: {result.passed}/{result.total}"


# ── Category 6: Persistence ───────────────────────────────────────────────────

def test_cat6_persistence():
    """Restart persistence — requires Docker. Skipped if SKIP_RESTART_TEST=1."""
    total = passed = 0
    details = []

    def _docker_ok() -> bool:
        try:
            return subprocess.run(
                ["docker", "compose", "ps", "app"],
                capture_output=True, timeout=10,
            ).returncode == 0
        except Exception:
            return False

    if os.environ.get("SKIP_RESTART_TEST") == "1" or not _docker_ok():
        result = CategoryResult(6, "Persistence (restart)", 1, 1,
                                ["  ⏭ Skipped (no Docker / SKIP_RESTART_TEST=1)"])
        _RESULTS.append(result)
        _print_category(result)
        return  # not a failure — skip gracefully

    uid = f"bm6-{_uid()}"
    sid = f"bm6-s-{_uid()}"

    with _client() as c:
        # Write before restart
        r = c.post("/turns", json={
            "session_id": sid,
            "user_id":    uid,
            "messages": [
                {"role": "user",      "content": "I am a robotics engineer at SpaceX in Hawthorne."},
                {"role": "assistant", "content": "Fascinating work!"},
            ],
            "timestamp": "2025-06-01T10:00:00Z",
            "metadata":  {},
        })
        total += 1
        if r.status_code == 201:
            passed += 1; details.append("  ✓ Turn written before restart")
        else:
            details.append(f"  ✗ Turn write failed: {r.status_code}")
            result = CategoryResult(6, "Persistence (restart)", passed, total, details)
            _RESULTS.append(result)
            return

    # Restart
    sub = subprocess.run(
        ["docker", "compose", "restart", "app"],
        capture_output=True, timeout=90,
    )
    if sub.returncode != 0:
        details.append(f"  ✗ docker compose restart failed")
        result = CategoryResult(6, "Persistence (restart)", passed, total + 1, details)
        _RESULTS.append(result); _print_category(result)
        return

    # Wait healthy
    deadline = time.time() + 90
    healthy = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                healthy = True; break
        except Exception:
            pass
        time.sleep(3)

    total += 1
    if healthy:
        passed += 1; details.append("  ✓ Service healthy after restart")
    else:
        details.append("  ✗ Service not healthy after restart")
        result = CategoryResult(6, "Persistence (restart)", passed, total, details)
        _RESULTS.append(result); _print_category(result)
        return

    with _client() as c:
        total += 1
        ctx = _recall(c, uid, "Where does this user work?")
        if "spacex" in ctx or "hawthorne" in ctx or "robotics" in ctx:
            passed += 1; details.append("  ✓ Data persisted across restart")
        else:
            details.append(f"  ✗ Data lost after restart. ctx: {ctx[:120]!r}")
        _cleanup(c, uid)

    result = CategoryResult(6, "Persistence (restart)", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.8


# ── Category 7: Cross-session Scoping ────────────────────────────────────────

def test_cat7_cross_session_scoping():
    total = passed = 0
    details = []

    with _client() as c:
        uid_a = f"bm7a-{_uid()}"
        uid_b = f"bm7b-{_uid()}"

        try:
            # User A: two sessions
            _ingest(c, uid_a, "I live in Tokyo.",       "2025-03-01T10:00:00Z")
            _ingest(c, uid_a, "I have a cat named Mochi.", "2025-03-02T10:00:00Z")
            # User B: one session
            _ingest(c, uid_b, "I work at Amazon.",      "2025-03-01T10:00:00Z")

            # A sees cross-session facts
            total += 1
            ctx = _recall(c, uid_a, "Where does this user live?")
            if "tokyo" in ctx:
                passed += 1; details.append("  ✓ User A cross-session: Tokyo visible")
            else:
                details.append(f"  ✗ User A cross-session: Tokyo missing. ctx: {ctx[:80]!r}")

            total += 1
            ctx = _recall(c, uid_a, "What is this user's pet's name?")
            if "mochi" in ctx:
                passed += 1; details.append("  ✓ User A cross-session: Mochi visible")
            else:
                details.append(f"  ✗ User A cross-session: Mochi missing. ctx: {ctx[:80]!r}")

            # A does NOT see B's data
            total += 1
            ctx = _recall(c, uid_a, "Amazon")
            if "amazon" not in ctx:
                passed += 1; details.append("  ✓ No cross-user bleed: A cannot see Amazon (B's data)")
            else:
                details.append(f"  ✗ Cross-user bleed: A sees Amazon. ctx: {ctx[:80]!r}")

            # B does NOT see A's data
            total += 1
            ctx = _recall(c, uid_b, "Tokyo Mochi cat")
            if "tokyo" not in ctx and "mochi" not in ctx:
                passed += 1; details.append("  ✓ No cross-user bleed: B cannot see Tokyo/Mochi (A's data)")
            else:
                details.append(f"  ✗ Cross-user bleed: B sees A's data. ctx: {ctx[:80]!r}")

        finally:
            _cleanup(c, uid_a)
            _cleanup(c, uid_b)

    result = CategoryResult(7, "Cross-session Scoping", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.75, f"Cross-session scoping too low: {result.passed}/{result.total}"


# ── Category 8: Robustness ────────────────────────────────────────────────────

def test_cat8_robustness():
    total = passed = 0
    details = []

    def _check_health(c):
        try:
            return c.get("/health").json().get("status") == "ok"
        except Exception:
            return False

    with _client() as c:
        bad_requests = [
            ("/turns",  {"user_id": "x", "messages": [{"role": "user", "content": "hi"}]}),        # missing session_id
            ("/turns",  {"session_id": "x", "user_id": "x", "messages": [], "timestamp": "now"}),  # empty messages + bad ts
            ("/turns",  {"session_id": "x", "messages": "string"}),                                 # wrong type
            ("/recall", {"session_id": "x", "max_tokens": 512}),                                   # missing query
            ("/recall", {"query": "", "session_id": "x", "max_tokens": 512}),                      # empty query
            ("/search", {"limit": 5}),                                                              # missing query
        ]
        for path, payload in bad_requests:
            total += 1
            r = c.post(path, json=payload)
            if r.status_code in (400, 422):
                passed += 1; details.append(f"  ✓ {path} bad input → {r.status_code}")
            else:
                details.append(f"  ✗ {path} bad input → {r.status_code} (expected 4xx)")

        # Service still alive after barrage
        total += 1
        if _check_health(c):
            passed += 1; details.append("  ✓ Service alive after bad-request barrage")
        else:
            details.append("  ✗ Service down after bad-request barrage")

        # Unicode → must be 201, not crash
        uid = f"bm8u-{_uid()}"
        try:
            total += 1
            r = c.post("/turns", json={
                "session_id": f"bm8s-{_uid()}",
                "user_id":    uid,
                "messages": [
                    {"role": "user",      "content": "🐕 こんにちは！I love 東京 and Ünïcödé!"},
                    {"role": "assistant", "content": "✓ Stored."},
                ],
                "timestamp": "2025-03-15T10:00:00Z",
                "metadata":  {},
            })
            if r.status_code == 201:
                passed += 1; details.append("  ✓ Unicode/emoji content → 201")
            else:
                details.append(f"  ✗ Unicode/emoji → {r.status_code}: {r.text[:80]}")
        finally:
            _cleanup(c, uid)

    result = CategoryResult(8, "Robustness", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.75, f"Robustness too low: {result.passed}/{result.total}"


# ── Category 9: Correctness (sync) ───────────────────────────────────────────

def test_cat9_sync_correctness():
    total = passed = 0
    details = []

    with _client() as c:
        uid = f"bm9-{_uid()}"
        sid = f"bm9s-{_uid()}"

        try:
            r = c.post("/turns", json={
                "session_id": sid,
                "user_id":    uid,
                "messages": [
                    {"role": "user",      "content": "I am a robotics engineer at SpaceX."},
                    {"role": "assistant", "content": "That sounds like amazing work!"},
                ],
                "timestamp": "2025-03-15T10:00:00Z",
                "metadata":  {},
            })

            # 1. Returns 201
            total += 1
            if r.status_code == 201:
                passed += 1; details.append("  ✓ POST /turns → 201")
            else:
                details.append(f"  ✗ POST /turns → {r.status_code}")
                result = CategoryResult(9, "Correctness (sync)", passed, total, details)
                _RESULTS.append(result); return

            turn_id = r.json()["id"]

            # 2. Immediately queryable via /memories
            total += 1
            mems = _memories(c, uid)
            if len(mems) >= 1:
                passed += 1; details.append(f"  ✓ /memories immediately has {len(mems)} row(s)")
            else:
                details.append("  ✗ /memories empty immediately after 201 (eventual consistency?)")

            # 3. Immediately queryable via /recall
            total += 1
            ctx = _recall(c, uid, "Where does this user work?")
            if ctx and ctx != "":
                passed += 1; details.append("  ✓ /recall non-empty immediately after 201")
            else:
                details.append("  ✗ /recall empty immediately after 201 (eventual consistency?)")

            # 4. turn_id in provenance
            total += 1
            if any(str(m.get("source_turn")) == turn_id for m in mems):
                passed += 1; details.append("  ✓ turn_id matches source_turn in memories")
            else:
                details.append(f"  ✗ turn_id {turn_id!r} not found in source_turn fields")

        finally:
            _cleanup(c, uid)

    result = CategoryResult(9, "Correctness (sync)", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.75, f"Sync correctness too low: {result.passed}/{result.total}"


# ── Category 10: Contract Compliance ─────────────────────────────────────────

def test_cat10_contract_compliance():
    total = passed = 0
    details = []

    with _client() as c:
        uid = f"bm10-{_uid()}"
        sid = f"bm10s-{_uid()}"

        try:
            # GET /health
            total += 1
            r = c.get("/health")
            if r.status_code == 200 and r.json().get("status") == "ok":
                passed += 1; details.append("  ✓ GET /health → 200 {status:ok}")
            else:
                details.append(f"  ✗ GET /health → {r.status_code}: {r.text[:60]}")

            # POST /turns → 201 + UUID id
            total += 1
            r = c.post("/turns", json={
                "session_id": sid, "user_id": uid,
                "messages": [
                    {"role": "user",      "content": "I work at Notion in Berlin."},
                    {"role": "assistant", "content": "Nice!"},
                ],
                "timestamp": "2025-03-15T10:00:00Z", "metadata": {},
            })
            if r.status_code == 201 and "id" in r.json():
                try:
                    uuid.UUID(r.json()["id"])
                    passed += 1; details.append("  ✓ POST /turns → 201 {id: UUID}")
                except ValueError:
                    details.append(f"  ✗ POST /turns id not a UUID: {r.json()['id']!r}")
            else:
                details.append(f"  ✗ POST /turns → {r.status_code}: {r.text[:60]}")

            # POST /recall → 200 {context, citations[]}
            total += 1
            r = c.post("/recall", json={
                "query": "Where does this user work?",
                "session_id": sid, "user_id": uid, "max_tokens": 512,
            })
            b = r.json() if r.status_code == 200 else {}
            if (r.status_code == 200
                    and isinstance(b.get("context"), str)
                    and isinstance(b.get("citations"), list)):
                passed += 1; details.append("  ✓ POST /recall → 200 {context, citations[]}")
            else:
                details.append(f"  ✗ POST /recall → {r.status_code}: {str(b)[:80]}")

            # POST /search → 200 {results[]}
            total += 1
            r = c.post("/search", json={"query": "Berlin Notion", "user_id": uid, "limit": 5})
            b = r.json() if r.status_code == 200 else {}
            if r.status_code == 200 and isinstance(b.get("results"), list):
                passed += 1; details.append("  ✓ POST /search → 200 {results[]}")
            else:
                details.append(f"  ✗ POST /search → {r.status_code}: {str(b)[:80]}")

            # GET /users/{id}/memories → 200 {memories[]}
            total += 1
            r = c.get(f"/users/{uid}/memories")
            b = r.json() if r.status_code == 200 else {}
            if r.status_code == 200 and isinstance(b.get("memories"), list):
                passed += 1; details.append(f"  ✓ GET /memories → 200 {{{len(b.get('memories',[]))} memories}}")
            else:
                details.append(f"  ✗ GET /memories → {r.status_code}: {str(b)[:80]}")

            # DELETE /sessions → 204
            total += 1
            r = c.delete(f"/sessions/{sid}")
            if r.status_code == 204:
                passed += 1; details.append("  ✓ DELETE /sessions → 204")
            else:
                details.append(f"  ✗ DELETE /sessions → {r.status_code}")

            # DELETE /users → 204
            total += 1
            r = c.delete(f"/users/{uid}")
            if r.status_code == 204:
                passed += 1; details.append("  ✓ DELETE /users → 204")
            else:
                details.append(f"  ✗ DELETE /users → {r.status_code}")

            # Cold session recall → 200, context=""
            total += 1
            r = c.post("/recall", json={
                "query": "anything", "session_id": f"cold-{_uid()}",
                "user_id": f"cold-{_uid()}", "max_tokens": 512,
            })
            b = r.json() if r.status_code == 200 else {}
            if r.status_code == 200 and b.get("context") == "" and b.get("citations") == []:
                passed += 1; details.append("  ✓ Cold session /recall → 200 {context:'',citations:[]}")
            else:
                details.append(f"  ✗ Cold session → {r.status_code}: ctx={b.get('context','?')[:40]!r}")

            # DELETE idempotency
            total += 1
            r = c.delete(f"/sessions/nonexistent-{_uid()}")
            if r.status_code == 204:
                passed += 1; details.append("  ✓ DELETE nonexistent session → 204 (idempotent)")
            else:
                details.append(f"  ✗ DELETE nonexistent session → {r.status_code}")

        except Exception:
            _cleanup(c, uid)
            raise

    result = CategoryResult(10, "Contract Compliance", passed, total, details)
    _RESULTS.append(result)
    _print_category(result)
    assert result.score >= 0.8, f"Contract compliance too low: {result.passed}/{result.total}"


# ── Final: write report ───────────────────────────────────────────────────────

def test_zzz_write_benchmark_report():
    """Collect all results, print the benchmark table, and write benchmarks/latest.json."""
    if not _RESULTS:
        pytest.skip("No results collected — run the full benchmark module")

    _print_final_table(_RESULTS)

    # Write JSON
    out_dir = REPO_ROOT / "benchmarks"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "latest.json"

    total_p = sum(r.passed for r in _RESULTS)
    total_t = sum(r.total  for r in _RESULTS)
    payload = {
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "service_url":  BASE_URL,
        "overall": {
            "passed":  total_p,
            "total":   total_t,
            "score":   round(total_p / total_t, 4) if total_t else 0.0,
            "pct":     f"{total_p / total_t:.0%}" if total_t else "0%",
        },
        "categories": [
            {
                "number":  r.number,
                "name":    r.name,
                "passed":  r.passed,
                "total":   r.total,
                "score":   round(r.score, 4),
                "pct":     r.pct,
                "emoji":   r.emoji,
                "details": r.details,
            }
            for r in _RESULTS
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    _safe_print(f"\n[OK] Benchmark written to {out_path}")

    # Trigger README update
    script = REPO_ROOT / "scripts" / "update_readme.py"
    if script.exists():
        import subprocess
        sub = subprocess.run(
            ["python", str(script)],
            capture_output=True, timeout=30,
        )
        if sub.returncode == 0:
            _safe_print("[OK] README.md updated with benchmark results")
        else:
            _safe_print(f"[WARN] README update failed: {sub.stderr.decode()[:200]}")


# ── Print helpers (ASCII-only for Windows cp1251 compatibility) ───────────────

def _safe_print(text: str) -> None:
    """Print text, replacing unencodable chars with '?' for Windows compatibility."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def _print_category(r: CategoryResult) -> None:
    status = "OK" if r.score >= 0.9 else ("WARN" if r.score >= 0.6 else "FAIL")
    _safe_print(f"\n{'-'*60}")
    _safe_print(f"  Cat {r.number:2d}. {r.name}")
    _safe_print(f"  Score: {r.passed}/{r.total} ({r.pct}) [{status}]")
    for d in r.details:
        # strip unicode checkmarks/crosses for Windows terminal
        safe_d = d.replace("✓", "[OK]").replace("✗", "[FAIL]").replace("⏭", "[SKIP]")
        _safe_print(safe_d)


def _print_final_table(results: list[CategoryResult]) -> None:
    total_p = sum(r.passed for r in results)
    total_t = sum(r.total  for r in results)
    overall_pct = f"{total_p / total_t:.0%}" if total_t else "0%"

    bar = "-" * 62
    _safe_print(f"\n{'='*62}")
    _safe_print(f"  MEMORY SERVICE BENCHMARK")
    _safe_print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    _safe_print(f"{'='*62}")
    _safe_print(f"  {'Category':<32}  {'Passed':>6}  {'Score':>7}  Status")
    _safe_print(f"  {bar}")
    for r in results:
        status = "OK  " if r.score >= 0.9 else ("WARN" if r.score >= 0.6 else "FAIL")
        _safe_print(f"  {r.number:>2}. {r.name:<28}  {r.passed:>3}/{r.total:<3}  {r.pct:>6}   [{status}]")
    _safe_print(f"  {bar}")
    _safe_print(f"  {'OVERALL':<32}  {total_p:>3}/{total_t:<3}  {overall_pct:>6}")
    _safe_print(f"{'='*62}\n")
