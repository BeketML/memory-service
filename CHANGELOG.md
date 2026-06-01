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
