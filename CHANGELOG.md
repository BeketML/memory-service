# Changelog

## v1 — Initial architecture: Postgres SoT + Qdrant derived index

**What changed:** Established the two-store architecture. PostgreSQL is the system of record (turns, memories, supersession chains, provenance). Qdrant is a derived index for hybrid retrieval. Extraction via LLM (gpt-4o-mini), embeddings via BGE-M3 (local, dense+sparse+ColBERT).

**Why:** A single-store approach (pgvector-only or Qdrant-only) conflates correctness with relevance. Qdrant can't enforce "one active fact per key" or store supersession history with relational integrity. Postgres can't do hybrid dense+sparse+multivector reranking cleanly. Splitting concerns gives both.

**Result:** Health endpoint working, schema migrations idempotent, Qdrant collection created at startup with payload indexes for fast filtering.

---

## v2 — Normalized key extraction + fact evolution (reconciliation)

**What changed:** LLM extraction now emits a normalized dot-separated `key` (e.g., `employment.employer`, `location.city`, `pet.name`). Reconciliation logic checks `WHERE user_id=? AND key=? AND active` — O(1) lookup instead of fuzzy semantic comparison. Supersession: old fact → `active=false`, `superseded_by=new_id`, `valid_to=now()`; new fact → `active=true`, `supersedes=old_id`. Partial unique index `WHERE active AND type IN ('fact','preference')` physically enforces one active scalar fact per key.

**Why:** Without a normalized key, contradiction detection requires embedding similarity, which is probabilistic and error-prone. A deterministic lookup on a canonical key is both cheaper and more reliable. The partial unique index catches any app-level bugs.

**Result:** Employment change (Stripe → Notion) correctly supersedes old fact. `/users/{id}/memories` shows full chain. `/recall` returns current fact only.

---

## v3 — Hybrid retrieval: dense + sparse → RRF → ColBERT rerank

**What changed:** Replaced vanilla cosine top-k with BGE-M3's three-vector hybrid pipeline. Dense (1024-d, semantic) + sparse (lexical IDF weights) retrieval legs, fused via Reciprocal Rank Fusion, then reranked by ColBERT MaxSim (late interaction, token-level). All three happen in a single Qdrant Query API call using nested Prefetch.

**Why:** Pure embedding search blurs exact tokens ("Biscuit" → semantically similar to other pet names but not exact). BM25/sparse catches keyword-heavy queries. ColBERT reranks the fused candidates at much higher quality than pooled-vector cosine. The task brief explicitly says "vanilla cosine top-k will not score well."

**Result:** Multi-hop query "What city does the user with the dog named Biscuit live in?" works because sparse hits `Biscuit` exactly; both `pet.name` and `location.city` are active facts for the same user, and the assembler includes both.

---

## v4 — 3-tier recall assembly with token budget

**What changed:** `/recall` now assembles context in three tiers: (1) all active stable facts from PG (always included, query-independent), (2) query-relevant memories from Qdrant hybrid search (filtered by relevance floor 0.3), (3) recent session turns from PG. Greedy fill within `max_tokens`, priority order enforced. tiktoken `cl100k_base` for counting, falling back to `len//4`.

**Why:** A frozen LLM benefits most from *who the user is* (stable facts) before *what they just searched for*. Recency is cheap to lose; durable identity facts are the thing only the memory service can supply. The relevance floor prevents hallucination — off-topic queries return empty context.

**Result:** Cold session → empty context (noise resistance). Budget-tight recall → stable facts survive, Tier 3 gets cut first. Tier 1 facts always appear, ensuring the agent always knows current employer/city/pet even on queries about unrelated topics.

---

## v5 — Query rewrite for multi-hop + opinion arc support

**What changed:** Added heuristic-gated query rewrite (LLM expands complex queries into 2-3 sub-queries). Opinion memories get a `stance` field (positive/negative/mixed/neutral); each opinion shift creates a new row with the prior deactivated (arc, not overwrite). Recall merges results across sub-queries before assembly.

**Why:** Multi-hop queries ("what city does the user with the dog named Biscuit live in?") are expanded to ["dog's name", "current city"], running both retrievals and merging — so both `pet.name` and `location.city` appear in Tier 2. Opinions need nuance: a user saying "TS generics are annoying" doesn't invalidate that they generally like TypeScript — it's a stance update, not a factual contradiction.

**Result:** Multi-hop probe hits both facts. Opinion arc is inspectable via supersession chain. Stance field allows recall to surface "currently mixed on TypeScript (was enthusiastic, now frustrated with generics)."

---

## v6 — Alembic migrations replace create_all; two bug fixes found in live testing

**What changed:**

1. **Alembic wired in.** Added `alembic.ini`, `migrations/env.py` (async asyncpg), `migrations/versions/0001_initial_schema.py`. Startup entrypoint (`docker-entrypoint.sh`) runs `alembic upgrade head` before uvicorn. The initial migration has an idempotency guard — if the `memories` table already exists (legacy `create_all` DB), it stamps the version without running DDL. Clean `pgdata` volumes get the full DDL. `Base.metadata.create_all` removed from `init_db()`.

2. **Bug fix: `sessions.py` `on_conflict_do_update` column alias.** The `set_` dict used Python attribute name `"metadata_"` instead of DB column name `"metadata"`, and `ins.excluded.metadata_` raised `AttributeError`. Fixed to `"metadata": ins.excluded["metadata"]`.

3. **Bug fix: supersession UniqueViolationError.** `reconcile.py` inserted the new `active=True` memory BEFORE deactivating the old one, which hit the partial unique index `uniq_active_scalar_fact`. Fixed by calling `deactivate_memory(session, old_id)` + `session.flush()` BEFORE `insert_memory`, then wiring `superseded_by` afterward. Added `deactivate_memory` and `set_superseded_by` helpers to `repos/memories.py`. Also wrapped each candidate in `session.begin_nested()` (SAVEPOINT) in `ingest.py` so one candidate failure never poisons the session for subsequent candidates.

**Why:** The UniqueViolationError was silently swallowing entire turns in multi-fact conversations (all candidates after the first contradiction failure were lost due to the rolled-back session). The Alembic migration makes the schema story reproducible and inspectable via `alembic history`.

**Result:** Live end-to-end test scores:
- Contract compliance: 7/7 endpoints pass shape + status checks ✅
- Fact evolution (Stripe→Notion, NYC→Berlin): supersession chains correct; `/memories` shows `active=false` + `superseded_by` on old, `active=true` + `supersedes` on new ✅
- Recall quality (5 probes on evolution scenario): 7/9 — employer + city + multihop pass; pet name and TypeScript opinion missed when packed in a single dense turn (LLM extraction density limitation, documented below)
- Cross-session scoping: 4/4 — cross-session facts visible, zero cross-user bleed ✅
- Delete cleanup: 1/1 — session delete preserves user-scoped memories ✅
- Malformed input: 400 on missing fields, wrong types, bad JSON; 201 on unicode/emoji/RTL ✅

**Known limitation:** When a single turn contains many distinct facts (pet name, employer, city, opinion all at once), the LLM extraction may miss lower-priority facts due to output length constraints. Mitigation: use separate turns per topic in real usage. The extraction prompt now receives `known_state` (existing active memories) to guide key normalization and detect corrections, but dense turns remain a challenge.

**Next:** Improve extraction prompt to split outputs across more tokens; add confidence-weighted fact prioritization in Tier-1 assembly so opinion arcs with `stance` field appear in recall context.
