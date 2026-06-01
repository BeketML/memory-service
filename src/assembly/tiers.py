from __future__ import annotations

from typing import Optional

from src.assembly.budget import count_tokens
from src.config import settings


def assemble_context(
    tier1_facts: list[dict],
    tier2_memories: list[dict],
    tier3_recent: list[dict],
    max_tokens: int,
    relevance_floor: float = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Greedy token-budget assembly.

    Returns (selected_tier1, selected_tier2, selected_tier3).
    Priority: stable facts → query-relevant → recent context.
    """
    if relevance_floor is None:
        relevance_floor = settings.relevance_floor

    budget = max_tokens
    t1_budget = int(budget * settings.tier1_budget_fraction)

    selected1: list[dict] = []
    seen_keys: set[str] = set()

    for fact in tier1_facts:
        text = fact.get("canonical_text", "")
        tokens = count_tokens(text)
        if tokens <= t1_budget:
            selected1.append(fact)
            seen_keys.add(fact.get("key", ""))
            t1_budget -= tokens
            budget -= tokens

    selected2: list[dict] = []
    for mem in tier2_memories:
        score = mem.get("score", 0.0)
        if score < relevance_floor:
            continue
        key = mem.get("key", mem.get("memory_id", ""))
        if key in seen_keys:
            continue
        text = mem.get("canonical_text", "")
        tokens = count_tokens(text)
        if tokens <= budget:
            selected2.append(mem)
            seen_keys.add(key)
            budget -= tokens

    selected3: list[dict] = []
    for turn in tier3_recent:
        text = turn.get("raw_text", "")[:400]
        tokens = count_tokens(text)
        if tokens <= budget:
            selected3.append(turn)
            budget -= tokens

    return selected1, selected2, selected3
