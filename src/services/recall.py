from __future__ import annotations

import logging
from typing import Optional

from src.assembly.formatter import build_citations, format_context
from src.assembly.tiers import assemble_context
from src.config import settings
from src.retrieval.embedder import embed
from src.retrieval.qdrant import hybrid_search
from src.retrieval.query_rewrite import expand_query
from src.storage.postgres.pool import get_pool
from src.storage.postgres.repos import memories as mem_repo
from src.storage.postgres.repos import turns as turn_repo

logger = logging.getLogger(__name__)


async def recall(
    query: str,
    session_id: str,
    user_id: Optional[str],
    max_tokens: int,
) -> dict:
    pool = await get_pool()

    # Tier 1: stable facts from PG (always)
    tier1: list[dict] = []
    if user_id:
        async with pool.acquire() as conn:
            tier1 = await mem_repo.get_active_stable_facts(conn, user_id)

    # Query expansion for multi-hop queries
    sub_queries = await expand_query(query)

    # Tier 2: hybrid search from Qdrant (all sub-queries, merge by score)
    tier2_raw: list[dict] = []
    seen_ids: set[str] = set()
    for q in sub_queries:
        try:
            emb = await embed(q)
            results = await hybrid_search(
                emb,
                user_id=user_id,
                session_id=session_id if not user_id else None,
                limit=settings.final_limit,
            )
            for r in results:
                mid = r.get("memory_id", "")
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    tier2_raw.append(r)
        except Exception as exc:
            logger.warning("Tier-2 search failed for query '%s': %s", q, exc)

    # Sort by descending score and filter by relevance floor
    tier2_raw.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    # Tier 3: recent session context
    tier3: list[dict] = []
    async with pool.acquire() as conn:
        tier3 = await turn_repo.get_recent_turns(conn, session_id, limit=3)

    # Assemble under budget
    selected1, selected2, selected3 = assemble_context(
        tier1_facts=tier1,
        tier2_memories=tier2_raw,
        tier3_recent=tier3,
        max_tokens=max_tokens,
    )

    context_str = format_context(selected1, selected2, selected3)
    citations = build_citations(selected2)

    return {"context": context_str, "citations": citations}
