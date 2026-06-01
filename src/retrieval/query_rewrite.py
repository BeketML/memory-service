from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_MULTIHOP_PATTERNS = re.compile(
    r"\b(with|who has|and|both|also|connecting|linked|related)\b",
    re.IGNORECASE,
)


def _needs_rewrite(query: str) -> bool:
    return bool(_MULTIHOP_PATTERNS.search(query)) or len(query) > 80


async def expand_query(query: str) -> list[str]:
    if not _needs_rewrite(query):
        return [query]

    try:
        from src.extraction.llm import call_llm

        system = (
            "You are a query expansion assistant. Given a recall query, "
            "output a JSON array of 2-3 specific sub-queries that would help "
            "find relevant memories. Each sub-query should be short and targeted. "
            "Return ONLY a JSON array of strings, no other text."
        )
        user = f"Query: {query}"
        raw = await call_llm(system, user, max_tokens=256)
        sub_queries = json.loads(raw.strip())
        if isinstance(sub_queries, list) and all(isinstance(q, str) for q in sub_queries):
            return [query] + sub_queries[:2]  # original + up to 2 expansions
    except Exception as exc:
        logger.debug("Query rewrite failed, using original: %s", exc)

    return [query]
