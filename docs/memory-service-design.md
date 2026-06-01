# Memory Service — Design Document

> AI agent memory service for the Higgsfield engineering challenge.
> Ingests conversation turns → extracts structured knowledge → answers recall queries.

---

## 0. Core idea (improved)

Original idea (yours):
> `/turns`: messages → text → LLM extraction → relevant facts as JSON → store + index in vector DB as **dense + sparse + multivector** → later do **hybrid search with reranker**.
> `/recall`: full hybrid-search pipeline.

This is the right backbone. The improvements below turn it from "vector-DB-out" into a real memory system the eval rewards:

1. **Two stores, one source of truth.**
   - **Postgres** is the *system of record*: raw turns, structured memories, supersession chains, provenance, history. It owns *correctness* and *fact evolution*.
   - **Qdrant** is a *derived index*: it makes memories *findable*. It can be rebuilt from Postgres at any time. It owns *retrieval*.
   - Rule: **never** answer "what is the current fact?" from Qdrant alone. Stable facts come from Postgres (always correct); Qdrant is for relevance ranking of the long tail.

2. **One embedding model for all three vectors: BGE-M3.**
   BGE-M3 emits dense (1024-d), sparse (lexical weights), and ColBERT multivector *in a single forward pass*. This is exactly your "dense + sparse + multivector" — without juggling three models. Defensible and cheap.

3. **Extraction is normalization, not summarization.**
   The LLM doesn't just pull out facts — it emits a **normalized `key`** (`employment.employer`, `location.city`, `pet.name`) and an **operation** (`add` / `update` / `correct` / `noop`). The `key` is what makes contradiction detection a cheap DB lookup instead of a fuzzy semantic guess.

4. **Recall is 3-tier, not top-k.**
   Stable facts (from Postgres, always) → query-relevant memories (hybrid + rerank from Qdrant) → recent session context. Token budget is spent in that order. This is the priority logic the README must defend.

5. **Hybrid = RRF fusion → ColBERT rerank.**
   Dense recall (semantics) + sparse recall (exact tokens like "Biscuit") fused with Reciprocal Rank Fusion, then reranked with ColBERT MaxSim (late interaction). Single Qdrant Query API call. This beats vanilla cosine top-k, which the brief explicitly penalizes.

---

## 1. High-level architecture

```
                         ┌──────────────────────────────────────────────┐
                         │              Memory Service (API)              │
                         │  FastAPI · synchronous · stateless app layer   │
                         └──────────────────────────────────────────────┘
        POST /turns                 │                          POST /recall · /search
            │                       │                                   │
            ▼                       │                                   ▼
   ┌──────────────────┐             │                       ┌──────────────────────┐
   │  Extraction       │            │                       │  Retrieval            │
   │  - flatten msgs   │            │                       │  - query rewrite      │
   │  - LLM → memories │            │                       │  - hybrid search      │
   │  - key normalize  │            │                       │  - ColBERT rerank     │
   │  - evolution      │            │                       │  - 3-tier assembly    │
   └──────────────────┘            │                        └──────────────────────┘
            │                       │                                   │
            ▼                       ▼                                   ▼
   ┌────────────────────────────────────────┐         ┌──────────────────────────────┐
   │  PostgreSQL  (system of record)         │         │  Qdrant  (derived index)      │
   │  users · sessions · turns · memories    │◀──sync──▶│  collection "memories"        │
   │  supersession chains · provenance       │  (write) │  dense + sparse + colbert     │
   └────────────────────────────────────────┘         └──────────────────────────────┘
          │  named docker volume  pgdata                       │  named docker volume  qdata
          └──────────── persistence across restarts ───────────┘

   BGE-M3 (dense+sparse+colbert)   ·   Extraction LLM (configurable)
```

**Write path (`/turns`)** is synchronous: persist turn → extract → reconcile facts → embed → upsert to Qdrant → return. Everything committed before `201`. No eventual consistency.

**Read path (`/recall`, `/search`)** reads stable facts straight from Postgres and ranks the long tail from Qdrant.

---

## 2. Backing store choice (and why)

| Concern | Store | Why |
|---|---|---|
| Source of truth, history, supersession | **PostgreSQL** | ACID, relational integrity (FKs, partial unique indexes enforce "one active fact per key"), JSONB for flexible metadata, trivially queryable for `/users/{id}/memories`. |
| Semantic + lexical retrieval | **Qdrant** | Native hybrid search via Query API (dense + sparse + multivector in one request), payload filtering for hard user/session scoping, RRF fusion + ColBERT rerank built in. |
| Embeddings | **BGE-M3** (via fastembed) | One model → dense + sparse + ColBERT. Local, no per-call cost, no rate limits. |
| Extraction / reconciliation | **LLM** (configurable, default `gpt-4o-mini`) | Structured JSON extraction + correction detection. Swappable to Anthropic / Ollama via env. |

Why not pgvector-only? It can do dense + FTS, but Qdrant gives first-class multivector late-interaction reranking and clean hybrid fusion out of the box — exactly the "deliberate ranking" the brief asks for. Why not Qdrant-only? It's a derived index; it has no notion of "the current fact" vs "superseded history". Splitting concerns keeps correctness in Postgres and relevance in Qdrant.

---

## 3. Data model — PostgreSQL schema

```sql
-- ───────────────────────── extensions ─────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ───────────────────────── users ──────────────────────────────
CREATE TABLE users (
    user_id      TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ───────────────────────── sessions ───────────────────────────
CREATE TABLE sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

-- ───────────────────────── turns (raw) ────────────────────────
CREATE TABLE turns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id      TEXT          REFERENCES users(user_id)       ON DELETE CASCADE,
    messages     JSONB        NOT NULL,        -- raw messages array, verbatim
    raw_text     TEXT         NOT NULL,        -- flattened "role: content" text
    turn_ts      TIMESTAMPTZ  NOT NULL,        -- timestamp from request body
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    metadata     JSONB        NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_turns_session  ON turns(session_id);
CREATE INDEX idx_turns_user_ts  ON turns(user_id, turn_ts DESC);

-- ───────────────────────── memories (extracted) ───────────────
CREATE TYPE memory_type AS ENUM ('fact', 'preference', 'opinion', 'event');

CREATE TABLE memories (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT        REFERENCES users(user_id)      ON DELETE CASCADE,
    session_id     TEXT        REFERENCES sessions(session_id) ON DELETE SET NULL,
    source_turn    UUID        REFERENCES turns(id)            ON DELETE CASCADE,

    type           memory_type NOT NULL,
    key            TEXT        NOT NULL,        -- normalized topic: 'employment.employer'
    value          TEXT        NOT NULL,        -- concrete value: 'Notion'
    canonical_text TEXT        NOT NULL,        -- NL statement for embedding + recall
    confidence     REAL        NOT NULL DEFAULT 0.7 CHECK (confidence BETWEEN 0 AND 1),
    stance         TEXT,                        -- opinions: positive|negative|mixed|neutral

    active         BOOLEAN     NOT NULL DEFAULT TRUE,
    supersedes     UUID        REFERENCES memories(id) ON DELETE SET NULL,
    superseded_by  UUID        REFERENCES memories(id) ON DELETE SET NULL,

    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_mem_user_active  ON memories(user_id, active);
CREATE INDEX idx_mem_user_key     ON memories(user_id, key);
CREATE INDEX idx_mem_source_turn  ON memories(source_turn);

-- Integrity guard: at most ONE active fact/preference per (user, key).
-- This is what makes "fact evolution" enforceable at the DB level.
CREATE UNIQUE INDEX uniq_active_scalar_fact
    ON memories(user_id, key)
    WHERE active = TRUE AND type IN ('fact', 'preference');

-- ───────────────────────── relations (optional, multi-hop) ────
-- For explicit entity linking. Not required for v1 since multi-hop
-- within a single user is solved by retrieving multiple facts and
-- letting the recall assembler connect them.
CREATE TABLE memory_relations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    from_memory UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_memory   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation    TEXT NOT NULL,               -- 'same_entity' | 'about' | 'co_occurs'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Schema design notes**

- **Memories are append-only except `active` / `superseded_by` / `updated_at`.** History is never destroyed — it's *deactivated*. This satisfies "store new as active, mark old as superseded — not deleted" and keeps the chain inspectable.
- **`key` is the contradiction-detection primitive.** Two memories about employment both carry `key='employment.employer'`; reconciliation is a `WHERE user_id=? AND key=? AND active` lookup, not a fuzzy match.
- **`canonical_text`** is a clean natural-language restatement (e.g. `"User works at Notion as a PM."`) used both as the Qdrant document and as the recall snippet — so retrieval and display are the same well-formed text.
- **Partial unique index** physically prevents two active scalar facts for the same key — a correctness safety net even if app logic has a bug.
- **`stance`** supports opinion arcs without forcing hard supersession (see §7).

---

## 4. Vector index — Qdrant collection

One collection: `memories`. Each Postgres memory → one Qdrant point (point `id` = memory `id`).

```python
# collection config (qdrant-client, pseudo)
client.create_collection(
    collection_name="memories",
    vectors={
        "dense":   VectorParams(size=1024, distance=Distance.COSINE),
        "colbert": VectorParams(                       # multivector / late interaction
            size=1024,
            distance=Distance.COSINE,
            multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM),
        ),
    },
    sparse_vectors={
        "sparse": SparseVectorParams(modifier=Modifier.IDF),
    },
)

# payload indexes for fast hard filtering
client.create_payload_index("memories", "user_id",    PayloadSchemaType.KEYWORD)
client.create_payload_index("memories", "session_id", PayloadSchemaType.KEYWORD)
client.create_payload_index("memories", "type",       PayloadSchemaType.KEYWORD)
client.create_payload_index("memories", "active",     PayloadSchemaType.BOOL)
```

**Point payload**

```json
{
  "memory_id": "uuid",
  "user_id": "user-1",
  "session_id": "sess-3",
  "type": "fact",
  "key": "location.city",
  "value": "Berlin",
  "canonical_text": "User lives in Berlin (moved from NYC).",
  "confidence": 0.9,
  "active": true,
  "created_at": 1710500000
}
```

**Hybrid query (single Query API call): dense + sparse → RRF → ColBERT rerank**

```python
client.query_points(
    collection_name="memories",
    prefetch=[
        Prefetch(query=dense_vec,  using="dense",  limit=40),
        Prefetch(query=sparse_vec, using="sparse", limit=40),
    ],
    query=FusionQuery(fusion=Fusion.RRF),     # stage 1: fuse lexical + semantic
    using="colbert",                          # stage 2: rerank survivors with MaxSim
    query=colbert_vecs,                       # (expressed as a nested rerank prefetch in real client)
    query_filter=Filter(must=[
        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        FieldCondition(key="active",  match=MatchValue(value=True)),
    ]),
    limit=20,
    with_payload=True,
)
```

> In the real client this is expressed as: outer `query=colbert` with a nested `prefetch` that itself fuses dense+sparse via RRF. Two retrieval signals, one fusion, one rerank — one round trip.

**Why these three signals**

- **dense** — paraphrase / semantic ("where does the user live?" ↔ "moved to Berlin").
- **sparse** — exact tokens that embeddings blur ("Biscuit", "Notion", error codes).
- **colbert** — token-level late interaction reranks the fused candidates far better than a single pooled vector, at low cost since it runs only on ~40 survivors.

---

## 5. Endpoint specifications (refined)

All endpoints: optional `Authorization: Bearer <MEMORY_AUTH_TOKEN>` (enforced only if the env var is set). Malformed input → `4xx`, never a crash.

### `GET /health`
Readiness probe. Returns `200 {"status":"ok"}` only when **both** Postgres and Qdrant are reachable and the collection exists; otherwise `503`. The eval polls this before starting.

---

### `POST /turns` — write + extract (synchronous)

**Internal workflow**

1. Validate body. Coerce missing `user_id` to `null`, missing `metadata` to `{}`. Upsert `users` / `sessions` rows (idempotent).
2. **Flatten** messages → `raw_text` (`"user: ...\nassistant: ...\ntool[name]: ..."`). Persist the `turns` row → `turn_id`. *Turn is now durable even if extraction later fails.*
3. **Load context for extraction:** current active memories for this `user_id` (so the LLM can detect corrections/contradictions relative to known state).
4. **LLM extraction** → JSON array of candidate memories, each with `{type, key, value, canonical_text, confidence, stance?, operation}` where `operation ∈ {add, update, correct, noop}`.
5. **Reconcile** each candidate against Postgres (see §6): insert new, supersede old, or skip duplicates — inside one DB transaction.
6. **Embed** each new/updated memory's `canonical_text` with BGE-M3 → dense + sparse + colbert. **Upsert** points to Qdrant; for superseded memories set `active=false` in their payload (or delete them — see §6 note).
7. Commit. Return `201 {"id": turn_id}`.

Everything is committed before the response, so the data is immediately queryable. The 60s budget comfortably covers extraction + embedding.

**Response:** `201 {"id":"<turn_id>"}` · errors `400` (bad shape) / `503` (store down).

---

### `POST /recall` — assembled context for the next agent turn (primary signal)

**Internal workflow**

1. **(Optional) query rewrite:** for multi-hop / vague queries, ask a cheap LLM to expand into sub-queries ("city of the user with dog Biscuit" → ["dog name", "city / location"]). Cheap, gated by a heuristic so simple queries skip it.
2. **Tier 1 — stable facts (Postgres):** `SELECT * FROM memories WHERE user_id=? AND active AND type IN ('fact','preference')`. Always candidates, regardless of query — these are the high-value, low-cost backbone.
3. **Tier 2 — query-relevant (Qdrant):** hybrid search (§4) filtered to `user_id` + `active`, returns reranked memories with scores. Apply a **relevance floor** (drop below threshold) for noise resistance.
4. **Tier 3 — recent context (Postgres):** latest events / recent turns for the session, newest first.
5. **Assemble under `max_tokens`** (see §8): Tier 1 → Tier 2 → Tier 3, greedy fill, dedup by `key`/`memory_id`.
6. Format readable markdown ("## Known facts about this user" / "## Relevant from recent conversations").
7. Build `citations` (`turn_id`, reranked `score`, `snippet`).

**Cold session / off-topic:** Tier 1 empty + Tier 2 below floor → return `200 {"context":"","citations":[]}`. Never error, never hallucinate.

**Response:** `200 {"context": "...", "citations": [...]}`.

---

### `POST /search` — explicit tool-call search (structured)

Same hybrid retrieval as `/recall` Tier 2, but **no prose assembly and no token budget** — returns a flat ranked list. `session_id`/`user_id` may be `null` (then scope by whichever is present; both null → global, still filtered to `active`).

**Response:**
```json
{ "results": [
  { "content":"User lives in Berlin (moved from NYC).", "score":0.83,
    "session_id":"sess-3", "timestamp":"2025-03-15T...", "metadata":{"type":"fact","key":"location.city"} }
]}
```

---

### `GET /users/{user_id}/memories` — inspection

`SELECT * FROM memories WHERE user_id=? ORDER BY key, created_at`. Returns **all** memories (active + superseded) so the supersession chain is visible (`supersedes` / `superseded_by` / `active`). Shape exactly as the contract reference. This is what the reviewers read to judge extraction quality — so it must show typed, structured rows, never raw chunks.

---

### `DELETE /sessions/{session_id}` → `204`
`DELETE FROM sessions WHERE session_id=?` (cascades to `turns`); delete Qdrant points by payload filter `session_id`. Note: user-scoped memories may outlive a single session — only delete memories whose `source_session` is this session, or keep them (document the choice). Default: delete turns + the session row + Qdrant points sourced from it; keep cross-session user facts.

### `DELETE /users/{user_id}` → `204`
`DELETE FROM users WHERE user_id=?` cascades to sessions/turns/memories; delete Qdrant points by filter `user_id`. Full wipe.

---

## 6. Fact evolution & contradiction handling

The whole mechanism rides on the normalized `key`.

**Reconciliation algorithm (per extracted candidate):**

```
candidate = {type, key, value, canonical_text, confidence, operation}

existing = SELECT * FROM memories
           WHERE user_id = :uid AND key = :key AND active = TRUE
           -- scalar facts: at most one row (enforced by unique index)

if existing is NULL:
    INSERT candidate (active = TRUE)                      -- brand new fact

elif normalize(existing.value) == normalize(candidate.value):
    -- same fact restated; bump confidence, refresh updated_at; no new row
    UPDATE existing SET confidence = max(...), updated_at = now()

else:  -- genuine contradiction / correction
    new = INSERT candidate (active = TRUE, supersedes = existing.id)
    UPDATE existing SET active = FALSE,
                        superseded_by = new.id,
                        valid_to = now()
    -- Qdrant: set existing point active=false (kept for /search history),
    --         upsert new point active=true
```

This gives the eval exactly what it checks:
- `/recall` returns **Notion** (active), not Stripe.
- `/users/{id}/memories` shows the chain: Stripe (`active=false`, `superseded_by=…`) → Notion (`active=true`, `supersedes=…`).
- History preserved; nothing deleted.

**Corrections** ("actually I meant X, not Y") are handled by the same path — the LLM emits `operation=correct` and the new value supersedes the wrong one (optionally with `confidence` boosted and the wrong row flagged in `metadata.correction=true`).

**Opinion arcs (harder).** Opinions are *not* hard-overwritten. For `type='opinion'` on the same `key` (e.g. `opinion.typescript`):
- Each statement is its own row with a `stance` (`positive` → `negative` → `mixed`).
- The latest is `active=true` for recall; prior ones stay `active=false` but linked via `supersedes`.
- Recall can surface the **arc**: *"Currently mixed on TypeScript (was enthusiastic in March, frustrated with generics later)."* Implementation can be partial: minimum = store the chain + present the latest stance; richer = summarize the trajectory in `canonical_text` at write time.

---

## 7. Recall ranking & token budget (priority logic)

**Ranking signal** per Tier-2 memory = ColBERT rerank score × `confidence` (and a small recency bonus). Stable facts (Tier 1) bypass ranking — they're always high priority.

**Budget assembly (defended in README):**

| Order | Tier | Rationale | Budget guard |
|---|---|---|---|
| 1 | Stable user facts (`fact`,`preference`, active) | Highest value-per-token, query-independent, what follow-ups most often depend on. | Reserve up to ~50% of `max_tokens`; always include at least the top-confidence facts. |
| 2 | Query-relevant memories (hybrid+rerank, above floor) | Directly answers the upcoming question; this is where multi-hop facts surface. | Fill remaining budget by descending score; dedup against Tier 1 by `key`. |
| 3 | Recent session context (events/turns) | Conversational continuity; least durable, so cut first. | Only if budget remains; newest first. |

Token counting: `tiktoken` if available, else `len(text)//4` heuristic. Greedy: add an item only if it fits; stop a tier when the next item would overflow, then move to the next tier with whatever remains. "Approximate is fine; don't blow past by 2×" → we never exceed `max_tokens`.

**Why this order:** when budget is tight, a frozen LLM benefits most from *who the user is* (stable facts) before *what they just talked about*. Recency is cheap to lose because the agent often already has it in its own window; durable identity facts are the thing only the memory service can supply.

---

## 8. End-to-end API ↔ DB interaction

### `POST /turns`

```
Client ─► API
  API ─► PG:   UPSERT users, sessions
  API ─► PG:   INSERT turns (raw_text, messages, turn_ts)  ──► turn_id
  API ─► PG:   SELECT active memories WHERE user_id        ──► known_state
  API ─► LLM:  extract(raw_text, known_state)              ──► [candidates]
  for each candidate:
      API ─► PG:  SELECT active memory by (user_id, key)
      API ─► PG:  INSERT new / UPDATE supersede (txn)
      API ─► M3:  embed(canonical_text) → dense, sparse, colbert
      API ─► QD:  upsert point(active=true); patch superseded point(active=false)
  API ─► PG:   COMMIT
API ◄─ 201 {id: turn_id}
```

### `POST /recall`

```
Client ─► API
  API ─► (LLM?): optional query rewrite → sub-queries
  API ─► PG:   SELECT active facts WHERE user_id            ── Tier 1
  API ─► M3:   embed(query) → dense, sparse, colbert
  API ─► QD:   hybrid query (RRF → ColBERT rerank, filter user_id+active) ── Tier 2
  API ─► PG:   SELECT recent events/turns WHERE session_id  ── Tier 3
  API:         assemble under max_tokens (Tier1→2→3), format markdown, build citations
API ◄─ 200 {context, citations}
```

### `DELETE /users/{id}`

```
Client ─► API
  API ─► PG:  DELETE FROM users WHERE user_id  (cascade: sessions, turns, memories)
  API ─► QD:  delete points by filter user_id
API ◄─ 204
```

**Consistency contract:** Qdrant writes happen inside the `/turns` request, before the response. If a Qdrant upsert fails, the request fails (`5xx`) and the PG transaction rolls back — so PG and Qdrant never silently diverge. A `POST /admin/reindex` endpoint can rebuild Qdrant from PG if they ever drift.

---

## 9. Full user story (sequence walkthrough)

A single user (`user-1`) across three sessions. Shows extraction, supersession, multi-hop, opinion arc, and noise resistance — and the DB state at each step.

### Session 1 (`sess-1`, 2025-03-01) — onboarding chat

**Turn:** user: *"Hey, I'm a backend engineer at Stripe. I've got a golden retriever named Biscuit and I'm living in NYC. Honestly I love TypeScript."*

**Extraction →**
| type | key | value | canonical_text | confidence | stance |
|---|---|---|---|---|---|
| fact | employment.employer | Stripe | User works at Stripe as a backend engineer. | 0.9 | — |
| fact | employment.role | backend engineer | User is a backend engineer. | 0.85 | — |
| fact | pet.name | Biscuit | User has a dog (golden retriever) named Biscuit. | 0.95 | — |
| fact | location.city | NYC | User lives in NYC. | 0.9 | — |
| opinion | opinion.typescript | loves | User loves TypeScript. | 0.8 | positive |

**Postgres `memories`:** 5 rows, all `active=true`.
**Qdrant:** 5 points (dense+sparse+colbert), payload `active=true`.

---

### Session 2 (`sess-2`, 2025-03-10) — debugging chat (noise + event)

**Turn:** user: *"My React dashboard re-renders way too much, profiler shows wasted renders."* / assistant: *"Sounds like missing memoization…"*

**Extraction →**
| type | key | value | canonical_text | confidence |
|---|---|---|---|---|
| event | event.debugging | React re-render perf | [2025-03-10] User debugged excessive re-renders in a React dashboard. | 0.7 |

No facts contradicted. One `event` row added (Tier-3 material for recall).

---

### Session 3 (`sess-3`, 2025-03-15) — life update (supersession + opinion arc)

**Turn:** user: *"Big news — I just started at Notion as a PM, and I moved to Berlin. Also, TS generics are getting really annoying lately."*

**Extraction + reconciliation →**
- `employment.employer`: existing `Stripe` (active) ≠ `Notion` → **supersede**.
  - Stripe row → `active=false`, `superseded_by=<notion_id>`, `valid_to=2025-03-15`.
  - Notion row → `active=true`, `supersedes=<stripe_id>`.
- `employment.role`: `backend engineer` → `PM` → **supersede** (same pattern).
- `location.city`: `NYC` → `Berlin` → **supersede**.
- `opinion.typescript`: new opinion, `stance=negative` ("annoying"). Prior "loves" → `active=false`, linked via `supersedes`; new active. Arc now: positive → negative.

**Postgres `memories` after Session 3 (employment.employer key):**
| id | value | active | supersedes | superseded_by |
|---|---|---|---|---|
| m1 | Stripe | false | — | m6 |
| m6 | Notion | true | m1 | — |

---

### Probe queries (what the eval would ask)

**Q1 — "Where does this user work now?"**
- Tier 1 returns active `employment.employer=Notion`.
- `/recall` context: *"Works at Notion as a PM (updated 2025-03-15; previously at Stripe as a backend engineer)."* ✅ current fact + history note.

**Q2 — "What city does the user with the dog named Biscuit live in?" (multi-hop)**
- sparse hits `Biscuit` (pet.name), dense + Tier-1 supply `location.city=Berlin`.
- Both are active facts for `user-1`; assembler includes both → answer connects them. ✅ no graph traversal needed because both facts share the user.

**Q3 — "How does the user feel about TypeScript?" (opinion arc)**
- Active opinion `stance=negative`; chain shows prior `positive`.
- `/recall`: *"Currently finds TypeScript generics annoying (previously enthusiastic about TS, ~March 1)."* ✅ arc surfaced.

**Q4 — "What's the user's favorite hiking trail?" (noise)**
- No memory clears the relevance floor; no relevant Tier-1 fact.
- `/recall` → `{"context":"","citations":[]}`. ✅ no hallucination.

**Q5 — `/users/user-1/memories`**
- Returns all rows incl. superseded Stripe/NYC/"loves TS" with intact chains. ✅ inspectable.

---

## 10. Failure modes & resilience

| Situation | Behavior |
|---|---|
| No data / cold session | `/recall` → `200 {"context":"","citations":[]}`. Never 500. |
| Missing `MEMORY_AUTH_TOKEN` | Auth disabled; all requests allowed. |
| Auth set but bad/absent token | `401`. |
| Malformed JSON / missing fields / wrong types | `400` with error body; service stays up. |
| Oversized payload | Truncate `raw_text` to a cap before extraction; `413` only if extreme. |
| Unicode oddities / emoji / RTL | Stored verbatim (Postgres `TEXT`, UTF-8); embeddings handle gracefully. |
| LLM extraction times out / errors | Turn is already persisted (step 2). Fall back: store turn as a single low-confidence `event` so it's still recallable; log and continue. Never lose the turn. |
| Qdrant upsert fails mid-`/turns` | Roll back PG transaction; return `5xx`. PG ↔ Qdrant stay consistent. `/admin/reindex` can rebuild. |
| Slow disk / slow embedding | `/turns` has 60s; `/recall` reads Tier-1 from PG first so it degrades to facts-only if Qdrant is slow. Document p95 + what to optimize (batch embeds, smaller candidate `limit`). |
| Restart mid-write | Uncommitted txn rolls back; committed turns survive (named volumes `pgdata`, `qdata`). Restart invisible to clients. |
| Concurrent sessions same user | Memories are user-scoped *by design* (cross-session knowledge sharing is intentional, documented). Turns/events are session-scoped and filtered — no bleed. |

---

## 11. Tech stack & models

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI + Uvicorn (Python) | async I/O, easy contract validation with Pydantic |
| RDBMS | PostgreSQL 16 | source of truth |
| Vector DB | Qdrant | hybrid + multivector |
| Embeddings | BGE-M3 via `fastembed` | dense 1024 + sparse + ColBERT, local |
| Extraction LLM | configurable (`gpt-4o-mini` default; Anthropic / Ollama via env) | JSON-structured output |
| Tokenizer | `tiktoken` (fallback char/4) | budget accounting |
| Deploy | `docker compose up` → app + postgres + qdrant, named volumes | port `8080` |

`.env.example`: `MEMORY_AUTH_TOKEN`, `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`/`OLLAMA_HOST`), `EMBEDDING_MODEL`.

---

## 12. Tradeoffs (for the README)

- **Two stores > one.** More moving parts, but clean separation of *correctness* (PG) and *relevance* (Qdrant). Worth it.
- **Synchronous extraction.** `/turns` is slower (LLM call inline) but the brief gives 60s and forbids eventual consistency — synchronous is the right call.
- **Local BGE-M3.** No API cost / rate limits and gets all three vectors, at the cost of container size + cold-start model load.
- **LLM extraction.** Best quality + correction handling, but adds latency and a key dependency; mitigated by the never-lose-the-turn fallback.
- **What we miss:** cross-*user* entity linking, fuzzy key collisions (two phrasings → different keys), and full opinion-arc summarization are partial in v1 — documented as next steps in the CHANGELOG.
