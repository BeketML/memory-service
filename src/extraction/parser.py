from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

VALID_TYPES = {"fact", "preference", "opinion", "event"}
VALID_OPERATIONS = {"add", "update", "correct", "noop"}
VALID_STANCES = {"positive", "negative", "mixed", "neutral", None}


@dataclass
class Candidate:
    type: str
    key: str
    value: str
    canonical_text: str
    confidence: float
    stance: Optional[str]
    operation: str


def _extract_json_array(raw: str) -> list:
    raw = raw.strip()
    # Try direct parse first
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return obj
        # json_object mode wraps in dict sometimes
        for v in obj.values():
            if isinstance(v, list):
                return v
        return []
    except json.JSONDecodeError:
        pass

    # Try to find a JSON array in the text
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return []


def parse_candidates(raw: str) -> list[Candidate]:
    items = _extract_json_array(raw)
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue

        type_ = str(item.get("type", "event")).lower()
        if type_ not in VALID_TYPES:
            type_ = "event"

        key = str(item.get("key", "")).strip().lower()
        if not key:
            continue

        value = str(item.get("value", "")).strip()
        if not value:
            continue

        canonical = str(item.get("canonical_text", value)).strip()
        if not canonical:
            canonical = value

        raw_conf = item.get("confidence", 0.7)
        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        stance = item.get("stance")
        if stance not in VALID_STANCES:
            stance = None

        op = str(item.get("operation", "add")).lower()
        if op not in VALID_OPERATIONS:
            op = "add"

        if op == "noop":
            continue

        result.append(
            Candidate(
                type=type_,
                key=key,
                value=value,
                canonical_text=canonical,
                confidence=confidence,
                stance=stance,
                operation=op,
            )
        )
    return result


def make_fallback_candidate(raw_text: str, turn_id: str) -> Candidate:
    snippet = raw_text[:300]
    return Candidate(
        type="event",
        key=f"event.turn_{turn_id[:8]}",
        value=snippet,
        canonical_text=f"[Auto-captured conversation] {snippet}",
        confidence=0.3,
        stance=None,
        operation="add",
    )
