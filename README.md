# Memory Service

A Dockerized HTTP memory service for AI agents. Ingests conversation turns, extracts structured knowledge, handles fact evolution, and answers recall queries with hybrid retrieval.

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │       Memory Service (FastAPI)         │
                    │    port 8080 · stateless app layer     │
                    └──────────────────────────────────────┘
   POST /turns              │                POST /recall · /search
       │                    │                        │
       ▼                    │                        ▼
┌─────────────┐             │              ┌──────────────────────┐
│  Extraction  │            │              │  Retrieval            │
│  - flatten   │            │              │  - query rewrite      │
│  - LLM→JSON  │            │              │  - hybrid search      │
│  - key norm  │            │              │  - ColBERT rerank     │
│  - evolution │            │              │  - 3-tier assembly    │
└─────────────┘             │              └──────────────────────┘
       │                    │                        │
       ▼                    ▼                        ▼
┌────────────────────────────────────┐   ┌────────────────────────────┐
│  PostgreSQL 16  (source of truth)   │   │  Qdrant  (derived index)   │
│  users · sessions · turns           │◀─▶│  collection "memories"     │
│  memories · supersession chains     │   │  dense + sparse + ColBERT  │
└────────────────────────────────────┘   └────────────────────────────┘
         named volume: pgdata                  named volume: qdata
```

Two stores, clean separation:
- **PostgreSQL** owns correctness, history, fact evolution, supersession chains. Never go to Qdrant to answer "what is the current fact?" — PG is always authoritative.
- **Qdrant** is a derived index for relevance ranking. It can be rebuilt from PG at any time via `POST /admin/reindex`.

## Backing Store Choice

**PostgreSQL 16** — ACID guarantees, relational integrity (foreign keys, partial unique index enforcing one active fact per key), JSONB for flexible metadata, straightforward for `/users/{id}/memories` inspection.

**Qdrant** — native hybrid search API (dense + sparse + multivector in one request), built-in RRF fusion and ColBERT late-interaction reranking, payload filtering for hard user/session scoping. Beats pgvector for this use case because Qdrant gives first-class multivector support and clean hybrid fusion out of the box.

Not pgvector-only: Qdrant's native hybrid query with RRF + ColBERT MaxSim rerank is exactly the "deliberate ranking" the task requires, and it has no notion of "current vs. superseded" — that lives in PG.

## Extraction Pipeline

**POST /turns** is synchronous: persist turn → extract → reconcile → embed → Qdrant upsert → return 201. The 60s budget comfortably covers the LLM call + embedding.

1. **Flatten** messages into `"role: content\n..."` text (tool messages included as `tool[name]: content`).
2. **Load known state** — current active memories for the user, so the LLM can detect contradictions.
3. **LLM extraction** (default: `gpt-4o-mini`, configurable via `LLM_PROVIDER`/`LLM_MODEL`) — system prompt asks for structured JSON with: `type`, `key`, `value`, `canonical_text`, `confidence`, `stance` (opinions), `operation`.
4. **Normalized key** — the central primitive: `employment.employer`, `location.city`, `pet.name`, `opinion.typescript`, etc. Contradiction detection is a cheap DB lookup (`WHERE user_id=? AND key=? AND active`), not fuzzy semantic matching.
5. **Fallback** — if LLM fails, a low-confidence `event` memory is stored so the turn is never lost.

**What we extract:** personal facts (employer, city, pets, family), preferences (diet, communication style, tools), opinions (with stance arc), significant events. Implicit facts ("walking Biscuit" → pet named Biscuit) are recognized by the LLM.

**What we miss:** cross-user entity linking, fuzzy key collisions (two phrasings mapping to different keys), full opinion-arc narrative summarization (we store the chain but don't synthesize prose). Documented as next steps.

## Recall Strategy

`POST /recall` is the primary signal. It assembles context in three tiers, spending the token budget in priority order:

| Tier | Source | Rationale | Budget |
|------|--------|-----------|--------|
| 1 | Stable facts + preferences (PG, always) | Highest value-per-token; query-independent; what follow-ups most often depend on | Up to 50% of `max_tokens` |
| 2 | Query-relevant memories (Qdrant hybrid + rerank) | Directly answers the upcoming question; multi-hop facts surface here | Remaining budget, descending score |
| 3 | Recent session turns (PG) | Conversational continuity; cut first when budget is tight | Whatever remains |

**Retrieval pipeline (Tier 2):**
1. Optional query rewrite for multi-hop queries (sub-query expansion via cheap LLM call — heuristic-gated, skips simple queries).
2. BGE-M3 embedding of query → dense (1024-d) + sparse (lexical weights) + ColBERT (token-level multivector).
3. Qdrant hybrid query: dense+sparse → **RRF fusion** (40 candidates each) → **ColBERT MaxSim rerank** → top 20.
4. Relevance floor (default 0.3) — results below are dropped. Noise resistance.
5. Dedup by `key` against Tier 1 to avoid repetition.

**Token counting:** tiktoken `cl100k_base`, falling back to `len(text)//4`. Greedy fill — never exceed `max_tokens`.

**Cold session / off-topic:** returns `200 {"context": "", "citations": []}`. Never errors, never halluccinates.

**Why this priority:** when budget is tight, a frozen LLM benefits most from *who the user is* (stable facts) before *what they just talked about*. Recency is cheap to lose because the agent often already has it in its own window; durable identity facts are the thing only the memory service can supply.

## Fact Evolution

The `key` field is the contradiction-detection primitive. Reconciliation algorithm:

```
existing = SELECT active memory WHERE user_id=? AND key=?

if no existing:   INSERT new (active=true)
elif same value:  UPDATE confidence (bump), no new row
else:             INSERT new (active=true, supersedes=old_id)
                  UPDATE old (active=false, superseded_by=new_id, valid_to=now)
                  Qdrant: set old point active=false; upsert new point
```

A partial unique index (`WHERE active AND type IN ('fact','preference')`) physically enforces one active fact per key — a DB-level safety net.

**History preserved:** the supersession chain is doubly linked (`supersedes` / `superseded_by`) and fully inspectable via `GET /users/{id}/memories`. Nothing is ever deleted.

**Opinion arcs:** each new opinion stance creates a new memory row with `active=true`; the prior row is deactivated but linked. Recall surfaces the current stance; the chain shows the arc.

**Corrections:** `operation=correct` in the LLM output follows the same supersession path, with `{"correction": true}` set in the memory's metadata.

## Tradeoffs

- **Two stores > one.** More moving parts, but clean separation of correctness (PG) and relevance (Qdrant). The `/admin/reindex` endpoint handles divergence.
- **Synchronous extraction.** `/turns` is slower (LLM + embed inline) but the brief gives 60s and forbids eventual consistency.
- **Local BGE-M3.** No API cost/rate limits; all three vectors in one model. Cost: ~2GB container image, slower cold start (mitigated by pre-download in Dockerfile).
- **LLM extraction.** Best quality + correction handling. Mitigated by the never-lose-the-turn fallback.
- **Qdrant failures are non-fatal.** If Qdrant is temporarily down, Tier 1 (PG facts) still serves recall. Tier 2 degrades until `/admin/reindex` rebuilds the index. Trade-off: brief period where new memories aren't in Tier 2 search.

## Failure Modes

| Situation | Behavior |
|-----------|----------|
| No data / cold session | `/recall` → `200 {"context":"","citations":[]}` |
| No `MEMORY_AUTH_TOKEN` | Auth disabled; all requests allowed |
| Auth set, bad token | `401` |
| Malformed JSON / missing fields | `400` / `422` with error body; service stays up |
| Oversized payload | `raw_text` truncated to 16K chars before extraction |
| Unicode / emoji / RTL | Stored verbatim (PG `TEXT`, UTF-8); BGE-M3 handles gracefully |
| LLM extraction timeout / error | Turn already persisted; fallback `event` memory stored; log + continue |
| Qdrant temporarily unavailable | Tier 1 (PG) recall still works; Tier 2 degrades gracefully; `/admin/reindex` fixes |
| Restart mid-write | Uncommitted PG transaction rolls back; committed data survives (named volumes) |
| Concurrent sessions, same user | Memories are user-scoped by design — cross-session fact sharing is intentional and documented |

## Running the Service

### Prerequisites

- Docker and Docker Compose
- An API key for your LLM provider (see `.env.example`)

### Quick Start

```bash
git clone <your-repo> memory-service
cd memory-service
cp .env.example .env
# Edit .env and set OPENAI_API_KEY (or ANTHROPIC_API_KEY / OLLAMA_HOST)

docker compose up -d
# Wait for health (image build includes BGE-M3 download — first build takes ~5min)
until curl -sf http://localhost:8080/health; do sleep 2; done
```

### Smoke Test

```bash
curl -s http://localhost:8080/health | jq .

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "smoke-1",
    "user_id": "user-1",
    "messages": [
      {"role": "user", "content": "I just moved to Berlin from NYC last month. Loving it so far."},
      {"role": "assistant", "content": "That sounds exciting! Berlin is a great city. How are you settling in?"}
    ],
    "timestamp": "2025-03-15T10:30:00Z",
    "metadata": {}
  }'

curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Where does this user live?",
    "session_id": "smoke-2",
    "user_id": "user-1",
    "max_tokens": 512
  }' | jq .

curl http://localhost:8080/users/user-1/memories | jq .
```

## Running Tests

Tests run against a live service on `http://localhost:8080`.

```bash
# Make sure the service is running
docker compose up -d
until curl -sf http://localhost:8080/health; do sleep 2; done

# Install test dependencies
pip install pytest httpx

# Run all tests
pytest tests/ -v

# Run specific suites
pytest tests/test_contract.py -v          # contract roundtrip
pytest tests/test_malformed.py -v         # input validation
pytest tests/test_concurrent.py -v        # cross-user isolation
pytest tests/test_recall_quality.py -v    # recall quality fixture
```

The recall quality test ingests 4 synthetic scenarios (employment change, opinion arc, implicit facts, noise resistance) and verifies that key facts appear in `/recall` responses. It reports a score (probes passed / total probes).

## API Reference

All endpoints accept and return JSON. Optional `Authorization: Bearer <MEMORY_AUTH_TOKEN>` header (enforced only if env var is set).

- `GET /health` — 200 when PG + Qdrant + collection ready; 503 otherwise
- `POST /turns` — write turn + extract + index; 201 `{"id":"<turn_id>"}`
- `POST /recall` — assembled context + citations; 200 `{"context":"...","citations":[...]}`
- `POST /search` — structured ranked results; 200 `{"results":[...]}`
- `GET /users/{user_id}/memories` — all memories (active + superseded); 200 `{"memories":[...]}`
- `DELETE /sessions/{session_id}` — 204
- `DELETE /users/{user_id}` — 204
- `POST /admin/reindex` — rebuild Qdrant from PG; 200 `{"reindexed": N}`
