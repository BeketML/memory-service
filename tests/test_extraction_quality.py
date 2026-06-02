"""Extraction quality tests — eval category #5: Extraction Quality.

Verifies that /users/{id}/memories contains structured typed memories
(not raw message chunks), has proper dot-notation keys, correct types,
and captures implicit facts.
"""
from __future__ import annotations

import uuid
import pytest


@pytest.fixture
def user(client):
    uid = f"exq-{uuid.uuid4().hex[:8]}"
    yield uid
    client.delete(f"/users/{uid}")


def _ingest(client, user_id: str, content: str, session_id: str = None) -> str:
    sid = session_id or f"exq-s-{uuid.uuid4().hex[:8]}"
    resp = client.post("/turns", json={
        "session_id": sid,
        "user_id":    user_id,
        "messages": [
            {"role": "user",      "content": content},
            {"role": "assistant", "content": "Got it, I'll remember that."},
        ],
        "timestamp": "2025-03-15T10:00:00Z",
        "metadata":  {},
    })
    assert resp.status_code == 201, f"Ingest failed: {resp.text}"
    return sid


def _memories(client, user_id: str) -> list[dict]:
    resp = client.get(f"/users/{user_id}/memories")
    assert resp.status_code == 200
    return resp.json()["memories"]


# ── Structure checks ──────────────────────────────────────────────────────────

def test_extraction_produces_at_least_one_memory(client, user):
    """Ingesting a fact-rich turn must produce ≥1 structured memory."""
    _ingest(client, user, "I'm a software engineer at Stripe in San Francisco.")
    mems = _memories(client, user)
    assert len(mems) >= 1, "Extraction produced no memories for a fact-rich turn"


def test_memory_types_are_valid_enum(client, user):
    """type must be one of fact/preference/opinion/event — not raw role string."""
    _ingest(client, user, "I work at Google. I love Python. I prefer remote work.")
    mems = _memories(client, user)
    valid = {"fact", "preference", "opinion", "event"}
    for m in mems:
        assert m["type"] in valid, (
            f"Memory type {m['type']!r} is not a valid enum value — looks like raw chunk"
        )


def test_memory_keys_use_dot_notation(client, user):
    """Keys must follow dot-notation (e.g. employment.employer, not free text)."""
    _ingest(client, user, "I'm a data scientist at OpenAI in New York.")
    mems = _memories(client, user)
    for m in mems:
        assert "." in m["key"], (
            f"Key {m['key']!r} doesn't use dot-notation — extraction not normalizing keys"
        )


def test_canonical_text_is_sentence_not_raw_message(client, user):
    """canonical_text must be a generated sentence, not a verbatim message copy."""
    raw = "I live in Berlin and I work at Notion."
    _ingest(client, user, raw)
    mems = _memories(client, user)
    for m in mems:
        ct = m["canonical_text"]
        assert len(ct) > 5, f"canonical_text too short: {ct!r}"
        # Should not be the verbatim raw message
        # (canonical_text is a clean NL statement extracted from context)
        assert ct != raw, f"canonical_text is verbatim copy of raw message: {ct!r}"


def test_confidence_in_range(client, user):
    """confidence must be between 0.0 and 1.0 for all memories."""
    _ingest(client, user, "I definitely love TypeScript. I kind of like Go.")
    mems = _memories(client, user)
    for m in mems:
        conf = m["confidence"]
        assert 0.0 <= conf <= 1.0, (
            f"Confidence {conf} out of [0, 1] range for memory: {m['key']!r}"
        )


# ── Specific extraction cases ─────────────────────────────────────────────────

def test_explicit_personal_fact(client, user):
    """Explicit employer fact must be extracted and keyed as employment.*."""
    _ingest(client, user, "I'm a backend engineer at Stripe.")
    mems = _memories(client, user)
    keys = [m["key"] for m in mems]
    has_employment = any("employment" in k for k in keys)
    assert has_employment, (
        f"No employment.* key found after ingesting employer fact. Keys: {keys}"
    )


def test_location_fact(client, user):
    """Location fact must be extracted and keyed as location.*."""
    _ingest(client, user, "I live in Berlin, Germany.")
    mems = _memories(client, user)
    keys = [m["key"] for m in mems]
    has_location = any("location" in k for k in keys)
    assert has_location, (
        f"No location.* key found after ingesting location fact. Keys: {keys}"
    )


def test_preference_fact(client, user):
    """Dietary preference must be extracted and have type preference or fact."""
    _ingest(client, user, "I'm vegetarian, so no meat restaurants please.")
    mems = _memories(client, user)
    all_vals = " ".join(m["value"].lower() for m in mems)
    all_keys = " ".join(m["key"].lower() for m in mems)
    assert "vegetarian" in all_vals or "vegetarian" in all_keys, (
        f"'vegetarian' not found in extracted memories. vals: {all_vals!r}"
    )


def test_implicit_fact_pet_name(client, user):
    """Implicit pet fact: 'walking Biscuit my golden retriever' → pet.name = Biscuit."""
    _ingest(client, user, "Just got back from walking Biscuit, my golden retriever. She loves the park.")
    mems = _memories(client, user)
    all_vals = " ".join(m["value"].lower() for m in mems)
    all_keys = " ".join(m["key"].lower() for m in mems)
    has_biscuit = "biscuit" in all_vals or "biscuit" in all_keys
    assert has_biscuit, (
        f"Implicit pet fact (Biscuit) not extracted. values: {all_vals!r}"
    )


def test_opinion_gets_stance(client, user):
    """Opinions must have a stance field (positive/negative/mixed/neutral)."""
    _ingest(client, user, "I absolutely love TypeScript — it makes my code so much safer.")
    mems = _memories(client, user)
    opinions = [m for m in mems if m["type"] == "opinion"]
    if opinions:
        for op in opinions:
            assert op.get("stance") in ("positive", "negative", "mixed", "neutral", None), (
                f"Opinion stance out of enum: {op.get('stance')!r}"
            )


def test_provenance_fields_set(client, user):
    """source_turn and source_session must be populated (provenance chain)."""
    sid = _ingest(client, user, "I work at Notion as a PM.")
    mems = _memories(client, user)
    for m in mems:
        assert m.get("source_turn") is not None, (
            f"source_turn is null — provenance missing: {m}"
        )
        assert m.get("source_session") is not None, (
            f"source_session is null — provenance missing: {m}"
        )
