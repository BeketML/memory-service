# Memory Service

A Dockerized HTTP memory service for AI agents (Higgsfield AI engineering challenge). Ingests conversation turns, extracts structured knowledge, handles fact evolution via supersession, and answers recall queries with hybrid retrieval and a token-budgeted 3-tier context assembler.

---

## Quick start

```bash
git clone <repo> memory-service
cd memory-service
cp .env.example .env
# Set OPENAI_API_KEY in .env (required for extraction + embeddings)

docker compose up -d
# First build downloads BGE-M3 ONNX models — takes ~5 min; subsequent starts are fast
until curl -sf http://localhost:8080/health; do sleep 2; done
```

Verify with the smoke test from §7 of the challenge brief:

```bash
curl -s http://localhost:8080/health | jq .
# → {"status":"ok"}

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "smoke-1",
    "user_id":    "user-1",
    "messages": [
      {"role":"user",      "content":"I just moved to Berlin from NYC last month. Loving it so far."},
      {"role":"assistant", "content":"That sounds exciting! Berlin is a great city."}
    ],
    "timestamp": "2025-03-15T10:30:00Z",
    "metadata":  {}
  }'
# → {"id":"<uuid>"}

curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"Where does this user live?","session_id":"smoke-2","user_id":"user-1","max_tokens":512}'
# → context mentions Berlin; optionally notes the move from NYC

curl http://localhost:8080/users/user-1/memories | jq .
# → structured rows: type=fact, key=location.city, value=Berlin, active=true
```

---

## Architecture

```
                 ┌──────────────────────────────────────────────┐
                 │           Memory Service (FastAPI)             │
                 │   port 8080 · stateless · Python 3.11         │
                 └──────────────────────────────────────────────┘
  POST /turns              │                    POST /recall · /search
      │                    │                            │
      ▼                    │                            ▼
┌────────────┐             │                ┌──────────────────────┐
│ Extraction │             │                │      Retrieval        │
│ flatten    │             │                │  query rewrite (LLM)  │
│ LLM → JSON │             │                │  BGE-M3 embed         │
│ key norm   │             │                │  hybrid: RRF + ColBERT│
│ reconcile  │             │                │  3-tier assembly      │
└────────────┘             │                └──────────────────────┘
      │                    │                            │
      ▼                    ▼                            ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│  PostgreSQL 16  (system of record)    │  │  Qdrant  (derived index)      │
│  users · sessions · turns             │◀▶│  collection "memories"        │
│  memories · supersession chains       │  │  dense + sparse + ColBERT     │
│  ACID · partial unique indexes        │  │  RRF fusion · MaxSim rerank   │
└──────────────────────────────────────┘  └──────────────────────────────┘
       named Docker volume: pgdata                named Docker volume: qdata
```

**Write path (`POST /turns`)** is fully synchronous: flatten messages → persist turn → load existing facts → LLM extraction → reconcile each candidate (insert/supersede) → BGE-M3 embed → upsert Qdrant → commit → return 201. Everything is committed before the response; there is no eventual consistency. A Qdrant upsert failure rolls back the Postgres transaction so the two stores never silently diverge.

**Read path (`POST /recall`, `POST /search`)** reads stable facts directly from Postgres (Tier 1 — always correct, no search needed) and ranks the long tail of query-relevant memories from Qdrant (Tier 2 — hybrid retrieval + ColBERT rerank) before assembling context within the `max_tokens` budget. Qdrant is never asked "what is the current fact?" — that question is always answered by Postgres.

---

## Backing store choice

### PostgreSQL 16 — system of record

Postgres owns **correctness**: ACID transactions, foreign-key cascades, `TIMESTAMPTZ` for temporal accuracy, JSONB for flexible metadata, and — most importantly — a partial unique index that physically enforces the core fact-evolution invariant:

```sql
CREATE UNIQUE INDEX uniq_active_scalar_fact ON memories(user_id, key)
    WHERE active = TRUE AND type IN ('fact', 'preference');
```

This means at most one active `fact` or `preference` per `(user_id, key)` can exist at the DB level, even if application logic has a bug. It also makes contradiction detection a deterministic O(1) keyed lookup instead of a fuzzy semantic comparison.

Postgres is also the natural store for the supersession chain (`supersedes` / `superseded_by` doubly-linked UUIDs), provenance (`source_turn` FK), and the full memory history that `GET /users/{id}/memories` exposes to reviewers.

### Qdrant — derived vector index

Qdrant owns **relevance**: it makes memories findable by semantic similarity and exact token match, but it is always a derived, rebuildable index. It was chosen over pgvector specifically for:

- **First-class hybrid query API** — dense + sparse + multivector in one request with built-in RRF fusion and ColBERT MaxSim reranking. Getting this from pgvector requires multiple queries and manual fusion.
- **Payload filtering** — hard scoping by `user_id` and `active=true` is handled inside the retrieval call, not as a post-filter.
- **ColBERT multivector** — token-level late-interaction reranking at low cost (runs only on ~40 RRF survivors, not the full index).

Qdrant can be rebuilt from Postgres at any time via `POST /admin/reindex`.

### Embedding model — BGE-M3 (local, via fastembed)

BGE-M3 produces **three embedding types in a single forward pass**: dense (1536-d, semantic), sparse (lexical BM25 weights), and ColBERT multivectors (128-d per token). This is exactly the "dense + sparse + multivector" pipeline the task asks for — without juggling three models or paying per-call API costs. The ONNX models are ~400 MB total and are downloaded once into the Docker image at build time.

Dense embeddings for the query (not memories) additionally use **OpenAI `text-embedding-3-small`** via API, so the query embedding benefits from OpenAI's high-quality model while memories use local BGE-M3.

### LLM — `gpt-4o-mini` (configurable)

Extraction uses a structured JSON prompt with `gpt-4o-mini` by default. This model is fast enough to fit well within the 60-second `/turns` budget while producing reliable structured output. Configurable to `claude-haiku-4-5-20251001` or Ollama via `.env`.

---

## Extraction pipeline

Raw turns become structured memories through five steps:

**1. Flatten messages.** All messages in the turn are concatenated as `"role: content\n..."`. Tool messages become `tool[name]: content`. Truncated at 16 000 chars before extraction.

**2. Load known state.** Current active memories for the user are serialized as JSON and injected into the extraction prompt. This lets the LLM detect corrections and contradictions relative to what is already known — critical for `operation=correct` detection.

**3. LLM extraction.** The model returns a JSON array of candidates:

```json
[
  {"type":"fact",    "key":"employment.employer", "value":"Notion",
   "canonical_text":"User works at Notion as a PM.",
   "confidence":0.95, "operation":"update"},
  {"type":"fact",    "key":"location.city",        "value":"Berlin",
   "canonical_text":"User lives in Berlin (moved from NYC).",
   "confidence":0.9,  "operation":"update"},
  {"type":"opinion", "key":"opinion.typescript",   "value":"TypeScript generics are annoying",
   "canonical_text":"User finds TypeScript generics annoying lately.",
   "confidence":0.8,  "stance":"negative", "operation":"update"}
]
```

**4. Normalized key.** The key (`employment.employer`, `location.city`, `pet.name`, `opinion.typescript`, …) is the single most important extraction primitive. It turns "is this a contradiction?" into a deterministic DB lookup. Two phrasings of the same fact must produce the same key; the LLM is instructed to do this and sees existing keys in `known_state`.

**5. Reconcile per candidate.** Each candidate is processed inside a SAVEPOINT so one failure cannot abort the remaining candidates. The reconciliation algorithm (detailed in the Fact Evolution section) inserts, bumps confidence, or supersedes as appropriate.

**What we extract:** personal facts (employer, city, country, pet names, family), preferences (diet, communication style, tool preferences), opinions (with stance arc), significant events. Implicit facts are recognized: *"walking Biscuit this morning"* → pet named Biscuit.

**What we miss / known limitations:**
- Dense single-turn messages with many facts simultaneously may miss lower-priority extractions due to LLM output length. Separate turns per topic is more reliable.
- Fuzzy key collisions: two phrasings that should map to the same key but don't. Mitigated by injecting `known_state` so the LLM reuses existing keys.
- Full opinion-arc narrative synthesis: the chain is preserved and the current stance is recalled, but we don't auto-generate a prose trajectory summary at write time.
- Cross-user entity linking (out of scope for this challenge).

**Fallback:** if LLM extraction fails (timeout, API error, parse error), the turn is already persisted in Postgres. A single low-confidence `event` memory is stored so the turn remains recallable. No turn is ever lost.

---

## Recall strategy

`POST /recall` is the primary evaluation signal. It assembles a formatted context string within `max_tokens` by pulling from three tiers in strict priority order.

### Tier 1 — stable facts (Postgres, always included)

```sql
SELECT * FROM memories
WHERE user_id = :uid AND active = TRUE AND type IN ('fact','preference')
ORDER BY confidence DESC, updated_at DESC
```

These are fetched directly from Postgres — no embedding, no search, no floor. They are always included because they represent the highest-value, lowest-volatility information the service holds about the user. Budget guard: up to 50% of `max_tokens`.

### Tier 2 — query-relevant memories (Qdrant hybrid + ColBERT rerank)

1. **(Optional) query rewrite.** For multi-hop or vague queries (heuristic-gated), a cheap LLM call expands the query into 2–3 sub-queries. *"What city does the user with the dog named Biscuit live in?"* → *["dog's name", "current city"]*. This runs both retrievals and merges ranked results.
2. **Embed query** with BGE-M3 → dense + sparse + ColBERT vectors.
3. **Hybrid Qdrant query:**
   ```
   Prefetch(dense, limit=50) + Prefetch(sparse, limit=50)
   → FusionQuery(RRF)             # fuse lexical + semantic
   → rerank with ColBERT MaxSim   # token-level late interaction
   → top 20, filter: user_id + active=true
   ```
4. **Relevance floor** (default 0.3): results below the threshold are dropped. This is the noise-resistance mechanism — off-topic queries return empty Tier 2.
5. **Dedup** against Tier 1 keys to avoid repeating what's already in context.

### Tier 3 — recent session context (Postgres)

Latest N turns from the current session, newest first. Provides conversational continuity. Cut first when budget is tight.

### Token budget and priority logic

```
budget = max_tokens
for fact in tier1_facts:            # stable user identity
    if fits(fact, budget):
        append; budget -= tokens(fact)

for mem in tier2_ranked:            # query-relevant
    if budget <= 0 or score < floor: break
    if fits(mem, budget):
        append; budget -= tokens(mem)

for turn in tier3_recent:           # conversational continuity
    if budget <= 0: break
    if fits(turn, budget): append; budget -= tokens(turn)
```

Token counting uses `tiktoken cl100k_base`; falls back to `len(text) // 4` if tiktoken is unavailable. The budget is never exceeded.

**Why this priority:** when budget is tight, a frozen LLM needs to know *who the user is* (stable facts) before it needs *what was just discussed* (recency). Durable identity facts (employer, city, preferences) are the thing only the memory service can supply — the agent already has recent context in its own context window. Recency is the cheapest thing to cut.

**Cold session / off-topic:** all three tiers empty → `200 {"context": "", "citations": []}`. Never errors, never hallucinates.

### Output format

```
## Known facts about this user
- User works at Notion as a PM. (as of 2025-03-15)
- User lives in Berlin (moved from NYC). (as of 2025-03-15)
- User has a golden retriever named Biscuit. (as of 2025-03-01)

## Relevant from recent conversations
- [2025-03-10] User was debugging a React performance issue with excessive re-renders.
```

---

## Fact evolution

The `key` field is the contradiction-detection primitive. Two memories with the same `(user_id, key)` are about the same fact. The reconciliation algorithm:

```
existing = SELECT * FROM memories WHERE user_id=? AND key=? AND active=TRUE

if existing is None:
    INSERT new memory (active=true)                         # brand-new fact

elif normalize(existing.value) == normalize(candidate.value):
    UPDATE existing SET confidence=max(...), updated_at=now()  # restated, no new row

else:  # genuine contradiction / correction
    UPDATE existing SET active=false, valid_to=now()        # deactivate old FIRST
    FLUSH                                                    # release unique-index slot
    INSERT new memory (active=true, supersedes=existing.id) # then insert new
    UPDATE existing SET superseded_by=new.id                # wire back-reference
    Qdrant: set old point active=false; upsert new point active=true
```

**Why deactivate-before-insert matters:** the partial unique index `WHERE active AND type IN ('fact','preference')` blocks an INSERT if the old row is still active. We flush the deactivation before inserting so the constraint is satisfied.

**History preserved:** no row is ever deleted. The doubly-linked chain (`supersedes` / `superseded_by`) is fully walkable and visible in `GET /users/{id}/memories`. `valid_from` / `valid_to` provide a temporal record.

**Example — employment change:**

```
GET /users/user-1/memories (after "I started at Notion as a PM"):

[
  {id:"m1", key:"employment.employer", value:"Stripe", active:false,
   superseded_by:"m2", valid_to:"2025-03-15T..."},
  {id:"m2", key:"employment.employer", value:"Notion", active:true,
   supersedes:"m1",    valid_from:"2025-03-15T..."}
]
```

**Corrections:** `operation=correct` in LLM output follows the same supersession path; the old memory gets `{"correction":true}` in its metadata.

**Opinion arcs:** opinions are not hard-overwritten. Each opinion shift creates a new row with the new `stance` (`positive` / `negative` / `mixed` / `neutral`) as `active=true`; the prior stance is deactivated but linked. Recall surfaces the current stance. The full arc is inspectable via `GET /users/{id}/memories` filtered to `type=opinion`.

---

## Cross-session scoping

**Design decision:** memories are user-scoped, not session-scoped. A user's facts are shared across all their sessions — this is intentional. When `user-1` says "I live in Berlin" in session 3, `/recall` with any session_id and `user_id=user-1` will return that fact.

Session-scoped data (turns, events) is filtered by `session_id` in Tier 3. User-scoped data (facts, preferences, opinions) is retrieved by `user_id` in Tiers 1 and 2.

**Cross-user isolation is absolute.** Qdrant queries always include a `user_id` hard filter. Postgres queries always include `WHERE user_id = :uid`. There is no code path that returns one user's memories to another user.

**Anonymous sessions** (`user_id: null`) are supported. Their extracted memories are session-scoped only and are not associated with any user.

---

## Tradeoffs

| Decision | Optimized for | Gave up |
|----------|--------------|---------|
| Two stores (PG + Qdrant) | Clean separation of correctness vs. relevance; Qdrant can be rebuilt from PG | More moving parts; two stores to keep in sync |
| Synchronous `/turns` | Strict consistency (no eventual consistency); data immediately queryable after 201 | Latency (60s budget used for LLM + embed inline); no async offload |
| Local BGE-M3 | Zero per-call cost; no rate limits; three vectors in one model | ~400 MB added to container image; slower cold start (mitigated by pre-download) |
| LLM extraction (gpt-4o-mini) | Best structured output quality; handles corrections and implicit facts | Adds ~1–3s latency per turn; requires API key; cost per ingestion |
| Partial unique index on active facts | DB-enforced fact evolution integrity; no double-active bugs | Requires deactivate-before-insert order in reconciliation |
| Normalized `key` for contradiction detection | O(1) deterministic lookup; no fuzzy semantic comparison | LLM must emit consistent keys; dense turns may produce inconsistent keys for the same concept |
| Tier 1 always included in recall | Stable facts never lost to token budget; agent always knows current employer/city/etc. | Recall context always non-empty for known users, even for off-topic queries (by design) |

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| No data / cold session | `/recall` → `200 {"context":"","citations":[]}` — never errors |
| `MEMORY_AUTH_TOKEN` not set | Auth disabled; all requests accepted |
| Auth set, missing/wrong token | `401 Unauthorized` |
| Malformed JSON / missing required fields | `400` with Pydantic error detail; service stays up |
| Wrong field types | `400`; service stays up |
| Unicode / emoji / RTL text | Stored verbatim (Postgres `TEXT` UTF-8); BGE-M3 handles Unicode gracefully; `201` |
| Oversized payload | `raw_text` truncated to 16 000 chars before LLM extraction |
| LLM extraction timeout / API error | Turn already persisted in PG (step 2); fallback `event` memory created; logged; no turn lost |
| Qdrant upsert fails mid-`/turns` | Postgres transaction rolls back → `503`; PG and Qdrant stay consistent; `/admin/reindex` rebuilds |
| Qdrant temporarily down | Tier 1 (PG facts) still serves `/recall`; Tier 2 degrades; `/admin/reindex` fixes after recovery |
| Missing `OPENAI_API_KEY` | LLM extraction fails on every turn → fallback events stored; recall works but lacks structured facts |
| Restart mid-write | Uncommitted PG transaction rolls back; committed turns + memories survive (named volumes) |
| Concurrent sessions, same user | Memories are user-scoped by design — cross-session sharing is intentional and documented above |

---

## Models and API keys

| Component | Model | Where | Notes |
|-----------|-------|-------|-------|
| Extraction LLM | `gpt-4o-mini` (default) | OpenAI API | Configurable via `LLM_PROVIDER` + `LLM_MODEL` |
| Dense query embedding | `text-embedding-3-small` | OpenAI API | Shares `OPENAI_API_KEY`; 1536-d |
| Sparse embedding (BM25) | `Qdrant/bm25` | Local (fastembed) | No API key; ~15 MB |
| ColBERT reranker | `colbert-ir/colbertv2.0` | Local (fastembed ONNX) | No API key; ~110 MB |
| Memory dense vectors | BGE-M3 | Local (fastembed) | 1024-d; ~400 MB total with ColBERT |

**Required environment variables** (see `.env.example`):

```
OPENAI_API_KEY=sk-...          # required for extraction + dense query embedding
```

**Optional:**

```
LLM_PROVIDER=anthropic         # openai (default) | anthropic | ollama
LLM_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-...

LLM_PROVIDER=ollama
LLM_MODEL=llama3.1
OLLAMA_HOST=http://host.docker.internal:11434

MEMORY_AUTH_TOKEN=secret       # if set, all requests require Bearer token
QDRANT_URL=http://host.docker.internal:6333  # use an existing Qdrant instead of the bundled one
```

If you already have a Qdrant instance running on `localhost:6333`, set `QDRANT_URL=http://host.docker.internal:6333` in `.env` and start only the app + postgres:

```bash
docker compose up -d postgres app
```

---

## Running the tests

### pytest (unit + integration)

Tests run against a live service on `http://localhost:8080`.

```bash
docker compose up -d
until curl -sf http://localhost:8080/health; do sleep 2; done

pip install pytest httpx
pytest tests/ -v

# Individual suites:
pytest tests/test_contract.py      -v   # contract roundtrip
pytest tests/test_malformed.py     -v   # input validation / 400s
pytest tests/test_concurrent.py    -v   # cross-user isolation
pytest tests/test_recall_quality.py -v  # recall quality fixture (4 scenarios, 12 probes)
```

The recall quality test ingests 4 synthetic scenarios (`fixtures/recall_quality.json`) and reports a pass rate (probes where expected facts appear in `/recall` context).

### Playwright e2e

Full end-to-end tests covering all 7 contract endpoints, fact evolution chains, multi-hop recall, noise resistance, cross-session scoping, and delete semantics. Custom scenarios live in `fixtures/custom_scenarios.json`.

```bash
cd e2e
npm install
npx playwright test

# Individual suites:
npx playwright test tests/contract.spec.ts   # all 7 endpoints
npx playwright test tests/evolution.spec.ts  # supersession chains
npx playwright test tests/multihop.spec.ts   # multi-hop recall
npx playwright test tests/noise.spec.ts      # noise / no hallucination
npx playwright test tests/scoping.spec.ts    # cross-session + delete
npx playwright test tests/malformed.spec.ts  # 400 robustness
```

Requires the service to be running on `http://localhost:8080`.

---

## Code map — how to navigate the source

The entire application lives under `src/`. Every package has one clear responsibility. Below is the map and the recommended reading order if you're understanding the code for the first time.

### Package map

```
src/
├── main.py                        ← FastAPI app + lifespan (startup wiring)
├── config.py                      ← All settings (env vars, model names, knobs)
│
├── api/                           ← HTTP layer only — no business logic here
│   ├── deps.py                    ← Shared FastAPI dependencies (auth, sessions)
│   ├── middleware/auth.py         ← Optional Bearer token middleware
│   └── routes/
│       ├── health.py              ← GET  /health
│       ├── turns.py               ← POST /turns
│       ├── recall.py              ← POST /recall
│       ├── search.py              ← POST /search
│       ├── memories.py            ← GET  /users/{id}/memories
│       ├── sessions.py            ← DELETE /sessions/{id}
│       ├── users.py               ← DELETE /users/{id}
│       └── admin.py               ← POST /admin/reindex
│
├── schemas/                       ← Pydantic request/response models (HTTP contract)
│   ├── turns.py                   ← TurnRequest, TurnResponse
│   ├── recall.py                  ← RecallRequest, RecallResponse, Citation
│   ├── search.py                  ← SearchRequest, SearchResponse, SearchResult
│   └── memories.py                ← MemoriesResponse, MemoryItem
│
├── services/                      ← Use-case orchestrators (one per endpoint group)
│   ├── ingest.py                  ← POST /turns pipeline: persist→extract→reconcile→embed→index
│   ├── recall.py                  ← POST /recall pipeline: tier1→tier2→tier3→assemble
│   ├── search.py                  ← POST /search pipeline: embed→hybrid search→format
│   └── delete.py                  ← DELETE /sessions and /users (PG + Qdrant cleanup)
│
├── extraction/                    ← LLM-based memory extraction
│   ├── prompts.py                 ← System prompt + user template for gpt-4o-mini
│   ├── llm.py                     ← Provider-agnostic LLM call (openai/anthropic/ollama)
│   └── parser.py                  ← Parse LLM JSON output → list[Candidate]; fallback logic
│
├── evolution/
│   └── reconcile.py               ← Fact reconciliation: insert / bump-confidence / supersede
│
├── retrieval/                     ← Vector search layer
│   ├── embedder.py                ← BGE-M3: dense + sparse + ColBERT in one pass
│   ├── qdrant.py                  ← upsert_memory, hybrid_search (RRF→ColBERT), patch_payload
│   └── query_rewrite.py           ← Optional LLM sub-query expansion for multi-hop
│
├── assembly/                      ← Context assembly under token budget
│   ├── tiers.py                   ← assemble_context(): greedy fill T1→T2→T3
│   ├── budget.py                  ← count_tokens() via tiktoken or len//4 fallback
│   └── formatter.py               ← format_context() → markdown; build_citations()
│
└── storage/
    ├── postgres/
    │   ├── models.py              ← SQLAlchemy ORM: User, Session, Turn, Memory, MemoryType
    │   ├── database.py            ← Async engine, session factory, health check
    │   └── repos/
    │       ├── users.py           ← upsert_user, delete_user
    │       ├── sessions.py        ← upsert_session, delete_session
    │       ├── turns.py           ← insert_turn, get_recent_turns
    │       └── memories.py        ← insert/supersede/deactivate/get_* — core data access
    └── qdrant/
        └── collection.py          ← init collection, payload indexes, health check
```

---

### Step-by-step reading order

Start here if you want to understand the system from top to bottom.

**Step 1 — Configuration and startup**

```
src/config.py          All knobs in one place: DB URL, Qdrant URL, LLM provider/model,
                       embedding model names, retrieval limits, budget fractions.
                       Read this first so you know every tuneable parameter.

src/main.py            FastAPI app creation, lifespan (startup order: PG → Qdrant →
                       embedding models), middleware wiring, router registration.
                       Shows how the whole service boots.
```

**Step 2 — Data model (the schema is the design)**

```
src/storage/postgres/models.py
                       Four tables: User, Session, Turn, Memory.
                       The Memory model is the core — read every column comment.
                       The partial unique index (uniq_active_scalar_fact) is the key
                       invariant that enforces fact evolution at the DB level.

migrations/versions/0001_initial_schema.py
                       Same schema in DDL form. The idempotency guard at the top
                       explains how Alembic handles an existing database.
```

**Step 3 — Write path (`POST /turns`)**

Follow the call chain top-to-bottom:

```
src/api/routes/turns.py          Route handler — validates input, calls ingest_turn()

src/services/ingest.py           Orchestrator — the most important file in the project.
                                 Read every step comment. This is where the entire write
                                 pipeline lives: flatten → persist turn → load known state
                                 → LLM extract → reconcile per candidate (savepoint) →
                                 embed → Qdrant upsert → return turn_id.

src/extraction/prompts.py        The system prompt. Shows exactly what the LLM is asked
                                 to produce: type, key, value, canonical_text, confidence,
                                 stance, operation. The key naming conventions table is
                                 critical for understanding contradiction detection.

src/extraction/llm.py            Provider-agnostic call (OpenAI / Anthropic / Ollama).
src/extraction/parser.py         JSON output → list[Candidate]; fallback event if parse fails.

src/evolution/reconcile.py       The reconciliation algorithm. Three branches:
                                 (1) new fact → INSERT, (2) same value → bump confidence,
                                 (3) contradiction → deactivate old + flush + INSERT new.
                                 The flush between deactivate and insert is the fix for the
                                 partial unique index constraint.

src/storage/postgres/repos/memories.py
                                 The actual DB calls: insert_memory, deactivate_memory,
                                 set_superseded_by, bump_confidence, get_active_memory_by_key.

src/retrieval/embedder.py        BGE-M3 wrapper: embed(text) → Embedding(dense, sparse, colbert).
src/retrieval/qdrant.py          upsert_memory(), patch_payload() (active=false on supersede).
```

**Step 4 — Read path (`POST /recall`)**

```
src/api/routes/recall.py         Route handler — calls recall() service.

src/services/recall.py           Orchestrator for the read path. Shows the three-tier
                                 assembly in about 40 lines. Clear structure:
                                 tier1 (PG facts) → expand query → tier2 (Qdrant) →
                                 tier3 (recent turns) → assemble → format → return.

src/retrieval/query_rewrite.py   Optional LLM sub-query expansion. Heuristic gate:
                                 only fires on long/complex queries. Read to understand
                                 when multi-hop expansion is triggered.

src/retrieval/qdrant.py          hybrid_search(): the Qdrant Query API call.
                                 Nested Prefetch (dense + sparse) → FusionQuery(RRF) →
                                 ColBERT rerank. One round-trip to Qdrant.

src/assembly/tiers.py            assemble_context(): greedy fill within max_tokens.
                                 Tier 1 gets up to 50% of the budget; Tier 2 fills the rest;
                                 Tier 3 gets whatever remains.

src/assembly/budget.py           count_tokens(): tiktoken cl100k_base or len//4 fallback.

src/assembly/formatter.py        format_context(): produces the markdown output ("## Known
                                 facts..."). build_citations(): maps Tier 2 hits to
                                 {turn_id, score, snippet}.
```

**Step 5 — Supporting endpoints**

```
src/services/search.py           POST /search — same Qdrant hybrid search as Tier 2,
                                 but no prose assembly, no budget. Returns flat list.

src/services/delete.py           DELETE /sessions and DELETE /users. PG cascade handles
                                 relational cleanup; Qdrant filter delete handles vectors.

src/api/routes/memories.py       GET /users/{id}/memories — straight PG SELECT returning
                                 all rows (active + superseded) for reviewers to inspect.

src/api/routes/admin.py          POST /admin/reindex — iterates all Memory rows in PG,
                                 re-embeds and re-upserts to Qdrant. Use after Qdrant recovery.
```

**Step 6 — Schemas (HTTP contract shapes)**

```
src/schemas/turns.py             TurnRequest validates: session_id, user_id, messages[],
                                 timestamp (ISO-8601), metadata.
src/schemas/recall.py            RecallRequest, RecallResponse, Citation shape.
src/schemas/search.py            SearchRequest (limit clamped 1-100), SearchResult shape.
src/schemas/memories.py          MemoryItem — the full contract shape for /memories.
```

**Step 7 — Tests and fixtures**

```
tests/test_contract.py           Contract roundtrip: /turns → /recall → shape checks.
tests/test_recall_quality.py     Ingests fixtures/recall_quality.json; reports X/Y probe score.
tests/test_concurrent.py         Two users, no cross-bleed assertions.
tests/test_malformed.py          Bad JSON, missing fields, unicode → 400/201 checks.
fixtures/recall_quality.json     4 scenarios (employment change, opinion arc, implicit facts,
                                 noise resistance) with expected facts per probe query.
fixtures/custom_scenarios.json   6 extended scenarios: fact evolution, multi-hop, opinion arc,
                                 noise, cross-session scoping, delete cleanup.
e2e/tests/evolution.spec.ts      Playwright: full supersession chain checks (Stripe→Notion).
e2e/tests/multihop.spec.ts       Playwright: connects pet.name + location.city for same user.
e2e/tests/scoping.spec.ts        Playwright: cross-session recall + cross-user isolation.
e2e/tests/noise.spec.ts          Playwright: no hallucination on off-topic queries.
e2e/tests/malformed.spec.ts      Playwright: robustness + unicode + RTL.
e2e/tests/contract.spec.ts       Playwright: all 7 endpoints, shape + status checks.
```

---

### How a single turn flows through the code

```
HTTP POST /turns
  └─ api/routes/turns.py          validates TurnRequest
     └─ services/ingest.py        orchestrates the pipeline
        ├─ storage/postgres/repos/users.py      upsert user (idempotent)
        ├─ storage/postgres/repos/sessions.py   upsert session (idempotent)
        ├─ storage/postgres/repos/turns.py      INSERT turn → turn_id
        ├─ storage/postgres/repos/memories.py   get_active_memories (known state)
        ├─ extraction/prompts.py   build prompt (known_state + raw_text)
        ├─ extraction/llm.py       call gpt-4o-mini → raw JSON string
        ├─ extraction/parser.py    parse → list[Candidate]
        └─ for each candidate (SAVEPOINT):
           ├─ evolution/reconcile.py      deactivate old (flush) → insert new → set_superseded_by
           ├─ retrieval/embedder.py       embed(canonical_text) → Embedding
           └─ retrieval/qdrant.py         upsert_memory() + patch_payload(old, active=false)
HTTP 201 {"id": "<turn_uuid>"}
```

### How a recall query flows through the code

```
HTTP POST /recall
  └─ api/routes/recall.py          validates RecallRequest
     └─ services/recall.py         orchestrates the read pipeline
        ├─ storage/postgres/repos/memories.py   get_active_stable_facts (Tier 1, always)
        ├─ retrieval/query_rewrite.py            optional: expand to sub-queries
        ├─ for each sub-query:
        │  ├─ retrieval/embedder.py              embed(query)
        │  └─ retrieval/qdrant.py                hybrid_search (RRF → ColBERT)
        ├─ storage/postgres/repos/turns.py       get_recent_turns (Tier 3)
        ├─ assembly/tiers.py                     assemble_context (greedy, T1→T2→T3)
        ├─ assembly/formatter.py                 format_context → markdown string
        └─ assembly/formatter.py                 build_citations → [{turn_id, score, snippet}]
HTTP 200 {"context": "...", "citations": [...]}
```

## Detailed endpoint pipelines

Each of the three main endpoints has a distinct internal pipeline. This section walks through every step with the exact file, function, and decision point involved.

---

### `POST /turns` — ingest pipeline

**Goal:** persist a raw conversation turn, extract structured memories from it, reconcile them against existing facts, and index everything so the data is immediately queryable.

**SLA:** synchronous — 201 is returned only after every step completes. The eval harness uses a 60-second timeout; the LLM call + embedding typically takes 3–8 seconds.

```
Client
  │
  │  POST /turns { session_id, user_id, messages[], timestamp, metadata }
  ▼
api/routes/turns.py  →  TurnRequest (Pydantic)
  │  Validates types; rejects bad JSON / missing fields with 400.
  │  Calls ingest_turn(session_id, user_id, messages, turn_ts, metadata)
  ▼
services/ingest.py

  ┌─ STEP 1: Upsert user + session (idempotent) ──────────────────────────────┐
  │  repos/users.py    → INSERT users ON CONFLICT DO NOTHING                  │
  │  repos/sessions.py → INSERT sessions ON CONFLICT DO UPDATE last_active_at │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ STEP 2: Flatten messages → raw_text ─────────────────────────────────────┐
  │  "user: ...\nassistant: ...\ntool[name]: ..."                             │
  │  Truncated to 16 000 chars if oversized.                                  │
  │  INSERT turns (messages, raw_text, turn_ts, metadata) → turn_id (UUID)   │
  │  ⚑ Turn is durable in PG from this point even if extraction fails.       │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ STEP 3: Load known state ─────────────────────────────────────────────────┐
  │  repos/memories.py → SELECT active memories WHERE user_id                 │
  │  Serialised to JSON and injected into the extraction prompt so the LLM    │
  │  can detect contradictions against what is already known.                  │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ STEP 4: LLM extraction ───────────────────────────────────────────────────┐
  │  extraction/prompts.py → build system prompt + user message:              │
  │    system: describes types, key naming conventions, operations             │
  │    user:   "CURRENT KNOWN STATE:\n{json}\nCONVERSATION TURN:\n{raw_text}" │
  │                                                                            │
  │  extraction/llm.py → call_llm(system, user, max_tokens=2048)              │
  │    OpenAI:    chat.completions (response_format=json_object, temp=0.0)    │
  │    Anthropic: messages.create (temp=0.0)                                  │
  │    Ollama:    POST /api/chat (stream=false)                                │
  │                                                                            │
  │  Returns raw JSON string, e.g.:                                           │
  │    {"memories": [                                                          │
  │      {"type":"fact","key":"location.city","value":"Berlin",               │
  │       "canonical_text":"User lives in Berlin (moved from NYC).",           │
  │       "confidence":0.9,"stance":null,"operation":"update"},               │
  │      {"type":"opinion","key":"opinion.typescript","value":"annoying",      │
  │       "canonical_text":"User finds TypeScript generics annoying.",         │
  │       "confidence":0.8,"stance":"negative","operation":"update"}           │
  │    ]}                                                                      │
  │                                                                            │
  │  extraction/parser.py → parse_candidates(raw)                             │
  │    Tries direct JSON parse → regex fallback for embedded arrays           │
  │    Validates each item: type ∈ {fact,preference,opinion,event},           │
  │    key non-empty, value non-empty, confidence clamped to [0,1]            │
  │    Discards operation=noop items                                           │
  │    Returns list[Candidate(type,key,value,canonical_text,confidence,       │
  │                           stance,operation)]                               │
  │                                                                            │
  │  ⚑ FALLBACK: if LLM fails / parse fails → make_fallback_candidate()      │
  │    Creates one event memory: key="event.turn_{turn_id[:8]}",              │
  │    value=first 300 chars of raw_text, confidence=0.3. Turn not lost.     │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ STEP 5: Reconcile + embed + index (per candidate, each in a SAVEPOINT) ──┐
  │                                                                            │
  │  evolution/reconcile.py → reconcile_candidate(candidate, user_id, ...)   │
  │                                                                            │
  │  Branch A — NO existing active memory for this (user_id, key):           │
  │    repos/memories.py → INSERT memory (active=true)                        │
  │                                                                            │
  │  Branch B — existing memory, SAME value (restated fact):                 │
  │    repos/memories.py → UPDATE confidence=max(old,new), updated_at=now()  │
  │    Returns (None, None) → no Qdrant upsert needed                         │
  │                                                                            │
  │  Branch C — existing memory, DIFFERENT value (contradiction/correction):  │
  │    ① repos/memories.py → UPDATE existing: active=false, valid_to=now()  │
  │    ② session.flush()   → propagate UPDATE so unique index slot is free   │
  │    ③ repos/memories.py → INSERT new memory (active=true,supersedes=old)  │
  │    ④ repos/memories.py → UPDATE existing: superseded_by=new_id           │
  │    Why flush before insert: the partial unique index                       │
  │    uniq_active_scalar_fact(user_id, key) WHERE active=true AND            │
  │    type IN ('fact','preference') would block the INSERT if the old row    │
  │    is still active=true. The flush releases the slot before inserting.    │
  │                                                                            │
  │  After reconcile → retrieval/embedder.py → embed(canonical_text):        │
  │    OpenAI text-embedding-3-small API  →  dense vector [1536-d]            │
  │    fastembed BM25 (Qdrant/bm25)       →  sparse indices + values          │
  │    fastembed ColBERT (colbertv2.0)    →  colbert [n_tokens × 128-d]       │
  │    Dense + local run concurrently via asyncio.gather for min latency.     │
  │                                                                            │
  │  retrieval/qdrant.py → upsert_memory(new_id, embedding, payload)         │
  │    PointStruct { id=memory_uuid, vector={dense,sparse,colbert},           │
  │                  payload={user_id,session_id,type,key,value,              │
  │                           canonical_text,confidence,active,created_at} }  │
  │                                                                            │
  │  If superseded: qdrant.py → patch_payload(old_id, {active: false})        │
  │    Keeps old point in Qdrant (for /search history) but marks inactive.   │
  │                                                                            │
  │  If candidate fails (any exception) → log warning, rollback SAVEPOINT,   │
  │  continue to next candidate. Other candidates in the turn are unaffected. │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ STEP 6: Commit + respond ─────────────────────────────────────────────────┐
  │  PG transaction commits. All memories immediately queryable.              │
  │  Return turn_id → HTTP 201 {"id": "<uuid>"}                               │
  └───────────────────────────────────────────────────────────────────────────┘
```

**Error cases:**
- Malformed request body → `400` (Pydantic `RequestValidationError` handler in `main.py`)
- Qdrant upsert fails → PG transaction rolls back → `503`. Stores stay consistent.
- LLM API unreachable → fallback event stored; `201` returned (turn not lost)
- PG unavailable → `503`

---

### `POST /recall` — retrieval pipeline

**Goal:** assemble the best possible context string for the agent's next turn, within `max_tokens`, using a 3-tier priority system.

**SLA:** should return within a few seconds. Tier 1 (PG) is cheap; Tier 2 (Qdrant + embeddings) is the main cost. If Qdrant is slow, Tier 1 alone still answers correctly.

```
Client
  │
  │  POST /recall { query, session_id, user_id, max_tokens }
  ▼
api/routes/recall.py  →  RecallRequest (Pydantic)
  │  Validates; calls recall(query, session_id, user_id, max_tokens)
  ▼
services/recall.py

  ┌─ TIER 1: Stable facts from Postgres (always, no search) ──────────────────┐
  │  repos/memories.py → get_active_stable_facts(user_id):                    │
  │    SELECT * FROM memories                                                  │
  │    WHERE user_id=:uid AND active=TRUE AND type IN ('fact','preference')   │
  │    ORDER BY confidence DESC, updated_at DESC                               │
  │                                                                            │
  │  These are always included regardless of the query — they represent the   │
  │  highest-value, lowest-volatility information the service holds.           │
  │  No embedding or search overhead. Max budget: 50% of max_tokens.          │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ QUERY REWRITE (heuristic-gated) ─────────────────────────────────────────┐
  │  retrieval/query_rewrite.py → expand_query(query)                         │
  │                                                                            │
  │  Triggers if: query matches _MULTIHOP_PATTERNS (contains "with", "who     │
  │  has", "and", "both", "also", "connecting", etc.) OR len(query) > 80     │
  │                                                                            │
  │  If triggered: LLM call → JSON array of 2-3 sub-queries                  │
  │    "What city does the user with the dog named Biscuit live in?"          │
  │    → ["dog's name", "current city or location"]                           │
  │                                                                            │
  │  Returns: [original_query] + sub_queries[:2]                              │
  │  If LLM fails or parse fails → [original_query] only (safe fallback)     │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ TIER 2: Hybrid Qdrant search (per sub-query, results merged) ────────────┐
  │  For each query in the sub-query list:                                    │
  │                                                                            │
  │  retrieval/embedder.py → embed(query)                                     │
  │    Runs OpenAI dense + local sparse+colbert concurrently (asyncio.gather) │
  │    Returns Embedding(dense[1536], sparse_indices[], colbert[n×128])       │
  │                                                                            │
  │  retrieval/qdrant.py → hybrid_search(embedding, user_id, session_id)     │
  │                                                                            │
  │    Qdrant Query API — single round-trip:                                  │
  │    ┌─────────────────────────────────────────────────────────────────┐    │
  │    │  Prefetch:                                                       │    │
  │    │    ① dense leg:  query=dense_vec, using="dense",   limit=50    │    │
  │    │    ② sparse leg: query=sparse_vec, using="sparse", limit=50    │    │
  │    │  → FusionQuery(RRF): merge ①+② with Reciprocal Rank Fusion    │    │
  │    │     survivors: top 20 (rerank_limit in config.py)              │    │
  │    │  → rerank survivors: query=colbert_vecs, using="colbert"       │    │
  │    │     ColBERT MaxSim: token-level late interaction               │    │
  │    │  → final limit: top 10 (final_limit in config.py)             │    │
  │    │  filter: user_id=<uid> AND active=true (hard scoping)         │    │
  │    └─────────────────────────────────────────────────────────────────┘    │
  │                                                                            │
  │    Why three signals:                                                      │
  │    • dense  → semantic ("where does user live?" ↔ "moved to Berlin")     │
  │    • sparse → exact tokens ("Biscuit", "Notion", error codes)             │
  │    • colbert → token-level reranking far outperforms pooled-vector cosine │
  │                                                                            │
  │  Results from all sub-queries are deduped by memory_id and merged.        │
  │  Sorted by score descending before assembly.                               │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ TIER 3: Recent session turns from Postgres ───────────────────────────────┐
  │  repos/turns.py → get_recent_turns(session_id, limit=3)                   │
  │    SELECT * FROM turns WHERE session_id=:sid                               │
  │    ORDER BY turn_ts DESC LIMIT 3                                           │
  │  Provides conversational continuity for the current session.               │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ ASSEMBLY: token-budget greedy fill ──────────────────────────────────────┐
  │  assembly/tiers.py → assemble_context(tier1, tier2, tier3, max_tokens)   │
  │                                                                            │
  │  budget = max_tokens                                                       │
  │  t1_budget = budget × 0.5   (tier1_budget_fraction in config.py)         │
  │                                                                            │
  │  For each Tier 1 fact (sorted by confidence DESC):                        │
  │    tokens = count_tokens(canonical_text)           ← tiktoken cl100k_base │
  │    if tokens ≤ t1_budget: add, t1_budget -= tokens, budget -= tokens      │
  │    else: skip (prevents one enormous fact from starving everything)        │
  │                                                                            │
  │  For each Tier 2 memory (sorted by score DESC):                           │
  │    if score < relevance_floor (0.3): break   ← noise filter               │
  │    if key already in Tier 1: skip             ← dedup                     │
  │    if tokens ≤ budget: add, budget -= tokens                              │
  │                                                                            │
  │  For each Tier 3 turn (newest first):                                     │
  │    text = raw_text[:400]                                                   │
  │    if tokens ≤ budget: add, budget -= tokens                              │
  │                                                                            │
  │  Result: (selected_t1, selected_t2, selected_t3)                          │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ FORMAT + CITATIONS ───────────────────────────────────────────────────────┐
  │  assembly/formatter.py → format_context(t1, t2, t3):                     │
  │                                                                            │
  │    "## Known facts about this user\n"                                     │
  │    "- User works at Notion as a PM. (as of 2025-03-15)\n"                │
  │    "- User lives in Berlin (moved from NYC). (as of 2025-03-15)\n"       │
  │    "\n## Relevant from memory\n"                                          │
  │    "- [2025-03-10] User debugged React re-render performance.\n"          │
  │                                                                            │
  │  assembly/formatter.py → build_citations(t2):                             │
  │    [ { "turn_id": "<uuid>", "score": 0.83, "snippet": "..." } ]          │
  │    turn_id = source_turn_id from Qdrant payload (provenance link)         │
  │                                                                            │
  │  Cold / no data: all three tiers empty → context="", citations=[]        │
  └───────────────────────────────────────────────────────────────────────────┘

  HTTP 200 { "context": "...", "citations": [...] }
```

**Error cases:**
- Missing `query` or empty string → `400`
- Qdrant unavailable → Tier 2 skipped with a warning; Tier 1 + Tier 3 still returned
- Unknown `user_id` → Tier 1 empty, Tier 2 filter returns nothing, empty context; `200 {"context":"","citations":[]}`

---

### `POST /search` — structured search pipeline

**Goal:** return a flat ranked list of memories for an agent tool call. No prose assembly, no token budget, no Tier 1/3. Pure Qdrant retrieval.

```
Client
  │
  │  POST /search { query, session_id?, user_id?, limit }
  ▼
api/routes/search.py  →  SearchRequest (Pydantic)
  │  limit clamped to [1, 100]; empty query → 400
  │  Calls search(query, session_id, user_id, limit)
  ▼
services/search.py

  ┌─ EMBED ────────────────────────────────────────────────────────────────────┐
  │  retrieval/embedder.py → embed(query)                                     │
  │  Same three-vector embedding as /recall (OpenAI dense + local sparse +   │
  │  local ColBERT), running concurrently.                                    │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ HYBRID SEARCH ────────────────────────────────────────────────────────────┐
  │  retrieval/qdrant.py → hybrid_search(embedding, user_id, session_id)     │
  │  Identical pipeline to Tier 2 of /recall:                                │
  │    dense + sparse → RRF → ColBERT MaxSim rerank                           │
  │                                                                            │
  │  Scoping rules (applied as Qdrant payload filters):                       │
  │    user_id provided  → filter by user_id  (ignore session_id)             │
  │    only session_id   → filter by session_id                               │
  │    both null         → filter active=true only (global search)            │
  │    always:           → active=true (inactive/superseded excluded)         │
  │                                                                            │
  │  No relevance floor — all results returned up to limit.                   │
  │  Includes superseded=false results only (active=true filter).            │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ SHAPE RESULTS ────────────────────────────────────────────────────────────┐
  │  For each Qdrant hit:                                                     │
  │    content   = payload.canonical_text                                     │
  │    score     = point.score (ColBERT MaxSim, rounded to 4 dp)             │
  │    session_id = payload.session_id                                        │
  │    timestamp = payload.created_at  (ISO-8601, set at upsert time)        │
  │    metadata  = { type, key, ...payload.metadata }                        │
  │                                                                            │
  │  HTTP 200 { "results": [ { content, score, session_id, timestamp,        │
  │                             metadata } ] }                                 │
  └───────────────────────────────────────────────────────────────────────────┘
```

**Difference from `/recall`:**

| | `/recall` | `/search` |
|-|-----------|-----------|
| Output | Formatted prose context | Flat ranked list |
| Token budget | Yes — greedy fill | No — raw results up to `limit` |
| Tier 1 (PG facts) | Always included | Not used |
| Tier 3 (recent turns) | Included if budget remains | Not used |
| Query rewrite | Heuristic-gated LLM expansion | No |
| Relevance floor | 0.3 (drops noise) | None |
| Use case | Agent context injection | Agent tool call / explicit search |

---

### `DELETE /sessions/{session_id}` and `DELETE /users/{user_id}`

```
DELETE /sessions/{id}
  │
  ├─ services/delete.py → delete_session(session_id)
  │    repos/sessions.py → DELETE FROM sessions WHERE session_id=:id
  │      PG CASCADE → turns deleted automatically (FK: session_id CASCADE)
  │      memories.session_id → SET NULL (memories survive, they're user-scoped)
  │    retrieval/qdrant.py → delete_by_filter({session_id: id})  [skipped — session
  │      scoped Qdrant points deleted if their payload.session_id matches]
  │
  │  Result: turns gone, memories remain with session_id=NULL
  └─ HTTP 204

DELETE /users/{id}
  │
  ├─ services/delete.py → delete_user(user_id)
  │    repos/users.py → DELETE FROM users WHERE user_id=:id
  │      PG CASCADE → sessions → turns → memories all deleted
  │    retrieval/qdrant.py → delete_by_filter({user_id: id})
  │      Qdrant FilterSelector deletes all points with this user_id
  │
  │  Result: full wipe — nothing remains in either store
  └─ HTTP 204
```

Both endpoints are idempotent — deleting a non-existent session or user returns 204 cleanly.

---

## HTTP API reference

Auth: optional `Authorization: Bearer <MEMORY_AUTH_TOKEN>`. All endpoints return JSON.

### `GET /health`
Returns `200 {"status":"ok"}` when Postgres and Qdrant are reachable and the collection exists. Returns `503` otherwise. Used as the readiness probe.

### `POST /turns` → 201
```json
// Request
{"session_id":"s1","user_id":"u1","messages":[{"role":"user","content":"..."},{"role":"assistant","content":"..."}],"timestamp":"ISO-8601","metadata":{}}
// Response
{"id":"<turn_uuid>"}
```
Synchronous: extracted memories are immediately queryable after the 201 response.

### `POST /recall` → 200
```json
// Request
{"query":"Where does this user live?","session_id":"s1","user_id":"u1","max_tokens":1024}
// Response
{"context":"## Known facts...\n- User lives in Berlin.","citations":[{"turn_id":"<uuid>","score":0.85,"snippet":"..."}]}
```
Returns `{"context":"","citations":[]}` for cold/unknown users. Never errors on no data.

### `POST /search` → 200
```json
// Request
{"query":"Berlin","session_id":null,"user_id":"u1","limit":10}
// Response
{"results":[{"content":"User lives in Berlin.","score":0.83,"session_id":"s1","timestamp":"ISO-8601","metadata":{"type":"fact","key":"location.city"}}]}
```

### `GET /users/{user_id}/memories` → 200
Returns all memories (active + superseded) for the user. Superseded memories have `active=false` and a populated `superseded_by` UUID.
```json
{"memories":[{"id":"<uuid>","type":"fact","key":"location.city","value":"Berlin","confidence":0.9,"source_session":"s1","source_turn":"<uuid>","created_at":"ISO","updated_at":"ISO","supersedes":null,"superseded_by":null,"active":true,"stance":null,"canonical_text":"User lives in Berlin."}]}
```

### `DELETE /sessions/{session_id}` → 204
Deletes the session and its turns. User-scoped memories (facts/preferences/opinions) are retained — their `session_id` is set to NULL. Qdrant points sourced from this session are deleted.

### `DELETE /users/{user_id}` → 204
Full wipe: cascades to all sessions, turns, and memories for the user. Deletes all Qdrant points for the user.

### `POST /admin/reindex` → 200
```json
{"reindexed": 42}
```
Rebuilds the Qdrant index from Postgres. Safe to call while the service is running. Use after Qdrant recovery or if the two stores drift.
