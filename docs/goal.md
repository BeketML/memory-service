/goal

## Project
Build the **Memory Service** for the Higgsfield AI engineering challenge: a Dockerized HTTP service that ingests conversation turns, extracts structured memories, handles fact evolution (supersession), and serves recall/search context for an AI agent.

**Read first (source of truth):**
- @docs/task.md — HTTP contract, hard constraints, submission format, required tests
- @docs/memory-service-design.md — architecture: Postgres (SoT) + Qdrant (index), BGE-M3, LLM extraction, 3-tier recall
- @docs/db-schema-and-diagrams.md — schema, endpoint flows, PG↔Qdrant consistency
- @docs/folder-structure.md — target layout under `src/`

Do not invent a different architecture unless docs are ambiguous; defend tradeoffs in README.

## Stack (from design)
- **API:** FastAPI (Python), port **8080**, `docker compose up` with no manual setup
- **Stores:** PostgreSQL 16 (system of record) + Qdrant (derived vector index), named volumes for persistence
- **Embeddings:** BGE-M3 (dense + sparse + ColBERT) via fastembed
- **Extraction:** configurable LLM (structured JSON: type, key, value, operation, canonical_text)
- **Recall:** hybrid retrieval (RRF) + rerank — not vanilla cosine top-k; 3-tier context assembly under `max_tokens`

## Required HTTP endpoints (exact contract in @docs/task.md)
- `GET /health` — 200 only when Postgres + Qdrant + collection are ready
- `POST /turns` — sync: after 201, data must be visible in `/recall` and `/users/{id}/memories`
- `POST /recall` — primary signal; formatted `context` + `citations`, respect `max_tokens`
- `POST /search` — structured ranked results (not prose)
- `GET /users/{user_id}/memories` — structured memories with supersession chain (not raw message chunks)
- `DELETE /sessions/{session_id}` — 204
- `DELETE /users/{user_id}` — 204
Optional: `POST /admin/reindex` to rebuild Qdrant from Postgres

## Implementation layout
Follow @docs/folder-structure.md under `src/`: `api/`, `schemas/`, `services/`, `extraction/`, `evolution/`, `retrieval/`, `assembly/`, `storage/postgres`, `storage/qdrant`.
Root deliverables: `README.md`, `CHANGELOG.md`, `docker-compose.yml`, `Dockerfile`, `.env.example`, `tests/`, `fixtures/`.

## Hard constraints (non-negotiable)
- Persistence across `docker compose down && up` (named volumes)
- **Synchronous correctness:** no eventual consistency after `POST /turns`
- Fact evolution: contradictions → supersede (history kept, `active=false`), current fact in `/recall`
- Real extraction (typed memories), not embedding raw turns as “memories”
- Graceful 4xx on malformed input; never crash on unicode/oversized payloads
- Document API keys in `.env.example`; optional `MEMORY_AUTH_TOKEN`

## Definition of done
1. `docker compose up` → health on `http://localhost:8080/health`
2. Smoke flow from @docs/task.md §7 works (Berlin turn → recall mentions Berlin → `/memories` shows structured rows)
3. `tests/` includes: contract roundtrip, restart persistence, concurrent sessions, malformed input, recall-quality fixture in `fixtures/`
4. `CHANGELOG.md` has iteration entries (what changed, why, observed metrics)
5. `README.md` covers architecture, stores, extraction, recall strategy, fact evolution, tradeoffs, failure modes, how to run tests

## Testing strategy (run after implementation)

### A. Service tests (`tests/` — pytest)
- Unit/integration for reconcile, assembly budget, schema validation
- Contract + persistence tests against API (can use `httpx` against running container)

### B. E2E via Docker + Playwright (`tests/e2e/` or `e2e/`)
1. `docker compose up -d --build`; wait until `GET /health` returns 200
2. Playwright `APIRequestContext` (or `fetch` in Playwright test) hits **each endpoint**:
   - Assert status codes and JSON shapes match @docs/task.md
   - Assert behavioral checks (not only shape): e.g. after supersession recall returns current employer; cold session recall returns empty context
3. **Synthetic scenarios** (create under `fixtures/` + use in E2E):
   - Multi-session user: employment change (Stripe → Notion), location (NYC → Berlin), pet name for multi-hop query
   - Opinion arc (TypeScript: positive → negative)
   - Noise query (unrelated topic → empty `context`)
   - Two `session_id`s, same `user_id` — cross-session facts visible; no cross-user bleed
   - `DELETE /sessions` vs `DELETE /users` cleanup behavior
4. Optional: restart container mid-suite → recall still returns pre-restart facts

Record expected facts per probe in fixture JSON; E2E asserts key strings appear in `context` or in `/memories` response.

## Workflow
- Implement in vertical slices: schema/migrations → `/turns` pipeline → `/recall` → remaining endpoints → tests → README/CHANGELOG
- After each major change, run pytest + Playwright E2E against docker-compose
- Update `CHANGELOG.md` when changing retrieval, extraction, or evolution logic

## Out of scope
Agent app, UI, multi-tenant production hardening, horizontal scaling proofs.

**Main goal:** ship a complete, eval-ready memory service per docs; verify every contract endpoint end-to-end on the Docker deployment using Playwright and synthetic fixture data.