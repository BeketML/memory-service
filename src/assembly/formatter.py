from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _fmt_date(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d")
    try:
        return str(ts)[:10]
    except Exception:
        return ""


def format_context(
    tier1: list[dict],
    tier2: list[dict],
    tier3: list[dict],
) -> str:
    sections: list[str] = []

    if tier1:
        lines = ["## Known facts about this user"]
        for fact in tier1:
            text = fact.get("canonical_text", "")
            updated = _fmt_date(fact.get("updated_at") or fact.get("valid_from"))
            if updated:
                lines.append(f"- {text} (as of {updated})")
            else:
                lines.append(f"- {text}")
        sections.append("\n".join(lines))

    if tier2:
        lines = ["## Relevant from memory"]
        for mem in tier2:
            text = mem.get("canonical_text", "")
            lines.append(f"- {text}")
        sections.append("\n".join(lines))

    if tier3:
        lines = ["## Recent conversation context"]
        for turn in tier3:
            ts = _fmt_date(turn.get("turn_ts"))
            raw = turn.get("raw_text", "")[:300]
            if ts:
                lines.append(f"- [{ts}] {raw}")
            else:
                lines.append(f"- {raw}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def build_citations(
    tier2: list[dict],
) -> list[dict]:
    citations = []
    for mem in tier2:
        turn_id = mem.get("source_turn_id") or mem.get("memory_id", "")
        citations.append({
            "turn_id": str(turn_id),
            "score": round(float(mem.get("score", 0.0)), 4),
            "snippet": mem.get("canonical_text", "")[:200],
        })
    return citations
