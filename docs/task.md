# Higgsfield AI Engineering Challenge

# 🧠 Build a Memory Service for an AI Agent

> **Role:** AI Engineer (mid-level)
> 
> 
> **Time-box:** 2 days of focused work
> 
> **Deliverable:** `A reproducible Git repository with a Dockerized memory service that conforms to the HTTP contract below.`
> 
> **Submission: Please submit your results here -** https://higgsfieldcareers.typeform.com/to/HoCFpdJC
> 

---

## 1. Overview

We're hiring an AI engineer. The task: design and build a **memory service** for an AI agent. The service ingests conversation turns, persists them, extracts structured knowledge, and answers recall queries that decide what context the agent sees on the next turn.

You build the service. We host it ourselves and run a private eval against it. You have full freedom over backing store, recall pipeline, extraction strategy, and language.

---

## 2. Your Task

Ship a single Docker-deployable memory service that:

1. Conforms to the HTTP contract in §3.
2. Persists data across container restarts via a Docker volume.
3. Comes up with `docker compose up` — no manual setup steps.
4. Has its own internal tests (including at least a small recall-quality fixture — see §7).
5. Ships a `CHANGELOG.md` documenting your iteration history (see §6).
6. Has a `README.md` explaining the architecture, the backing store choice, and the recall strategy.

You have full design freedom on:

- **Language / framework:** Python, Go, Rust, TypeScript — anything Docker can run.
- **Backing store:** Postgres + pgvector, SQLite + FTS, Qdrant, Redis, Mongo, flat files with a clever index — defend it.
- **Extraction pipeline:** How you turn raw conversation turns into structured, queryable knowledge. LLM-based (any provider — OpenAI, Anthropic, local via Ollama, etc.), rule-based, NLP, hybrid — your call. Raw-message-in-vector-DB-out is not extraction.
- **Recall pipeline:** embeddings, BM25, hybrid, graph traversal, rerankers, query rewriting. **Vanilla cosine-top-k will not score well.**
- **Internal architecture:** monolith, multi-service — your call.

---

## 3. The HTTP Contract

Your service must expose these endpoints. Auth is via an optional `Authorization: Bearer <token>` header (we'll set `MEMORY_AUTH_TOKEN` if you require one and ignore it if you don't).

---

### `GET /health`

Liveness/readiness probe. Returns 200 when ready to serve traffic.

---

### `POST /turns`

Write a completed conversation turn. The service should persist the turn, run extraction, and return when done. Our eval harness uses a **60-second timeout** for this endpoint — take as long as you need for extraction, embedding, and indexing. Don't waste time on async orchestration; focus on extraction quality.

**Request:**

```
{
  "session_id":"string",
  "user_id":"string | null",
  "messages": [
    { "role":"user", "content":"string" },
    { "role":"assistant", "content":"string" },
    { "role":"tool", "name":"string | null", "content":"string" }
  ],
  "timestamp":"ISO-8601 string",
  "metadata": { "...":"..." }
}
```

A turn may contain one or more messages. The simplest case is a user message followed by an assistant response. Multi-message turns (including tool calls) are valid. Your service must handle both gracefully.

**Response:** `201 Created`, body `{ "id": "string" }`. The turn and any extracted memories must be queryable via `/recall` immediately after this returns.

---

### `POST /recall`

Return relevant context for the upcoming agent turn. This is the **primary signal** — most of the eval scores this endpoint.

**Request:**

```
{
  "query":"string",
  "session_id":"string",
  "user_id":"string | null",
  "max_tokens":1024
}
```

**Response:**

```
{
  "context":"string",
  "citations": [
    { "turn_id":"string", "score":0.0, "snippet":"string" }
  ]
}
```

**Behavior:**

- `context` is the formatted text injected into the agent's prompt. Make it readable to a frozen LLM. Example:

```
## Known facts about this user
- Works at Notion as a PM (updated 2025-03-15; previously at Stripe as an engineer)
- Vegetarian, allergic to shellfish
- Has a dog named Biscuit
- Prefers concise, direct answers

## Relevant from recent conversations
- [2025-03-10] User was debugging a React performance issue with excessive re-renders in a dashboard component
- [2025-03-14] User mentioned preparing for a system design interview at a FAANG company
```

- Should respect `max_tokens` (approximate is fine; don't blow past it by 2×).
- When budget is tight, prioritize: stable user facts first, then query-relevant memories, then recent context. Your priority logic is a design decision we care about — defend it in the README.
- Returns 200 with `{"context": "", "citations": []}` if nothing is relevant — never error on cold sessions.

---

### `POST /search`

Explicit search invoked by an agent tool call. Different shape from `/recall` (structured results vs. formatted prose).

**Request:**

```
{
  "query":"string",
  "session_id":"string | null",
  "user_id":"string | null",
  "limit":10
}
```

**Response:**

```
{
  "results": [
    {
      "content":"string",
      "score":0.0,
      "session_id":"string",
      "timestamp":"ISO-8601",
      "metadata": {}
    }
  ]
}
```

---

### `GET /users/{user_id}/memories`

Return all stored memories for a user. Used for debugging and inspection.

**Response:**

```
{
  "memories": [
    {
      "id":"string",
      "type":"fact | preference | opinion | event",
      "key":"string",
      "value":"string",
      "confidence":0.0,
      "source_session":"string",
      "source_turn":"string",
      "created_at":"ISO-8601",
      "updated_at":"ISO-8601",
      "supersedes":"string | null",
      "active":true
    }
  ]
}
```

The exact schema is up to you — the above is a reference, not a requirement. But we will call this endpoint during review to inspect what your system extracted, so it must exist and return structured data.

---

### `DELETE /sessions/{session_id}`

Used by the eval for cleanup between scenarios. Delete all data associated with a session. Returns `204 No Content`.

---

### `DELETE /users/{user_id}`

Used by the eval for cleanup between scenarios. Delete all data associated with a user (memories, turns, sessions). Returns `204 No Content`.

---

You may add more endpoints (admin, metrics, rebuild). The seven above are the contract.

---

## 4. The Hard Problems

These are the problems that separate thoughtful submissions from boilerplate.

---

### Fact evolution and contradiction handling

A user says "I work at Stripe" in session 1 and "I just started at Notion" in session 3. Your service must:

- Detect that these are about the same topic (employment).
- Store the new fact as active and mark the old one as superseded — not deleted.
- Return the current fact ("works at Notion") from `/recall`, not the stale one.
- Preserve the history (inspectable via `/users/{user_id}/memories`).

This applies to all mutable facts: location, job, relationships, opinions, preferences. A pure append-only log with no update logic will score poorly on our eval.

Harder variant: opinions evolve gradually. "I love TypeScript" → "TypeScript generics are getting annoying" → "TypeScript is fine for big projects but I'd use Python for scripts." This isn't a simple overwrite — it's an arc. Document how your system handles this even if the implementation is partial.

---

### Extraction, not just storage

The difference between a memory service and a message log is **extraction**. Your service should derive structured knowledge from raw conversations, not just store and retrieve message text.

At minimum, recognize and extract:

- Personal facts (employment, location, family, pets)
- Preferences and opinions
- Corrections ("actually, I meant..." or "sorry, not X — Y")
- Implicit facts ("walking Biscuit this morning" → has a pet named Biscuit)

We inspect `/users/{user_id}/memories` during review. If it returns raw message chunks instead of structured memories, that's a red flag.

---

### Context assembly under budget

`/recall` must return context that fits within `max_tokens`. When budget is tight, you're making triage decisions: what gets included and what gets cut? This priority logic — and your ability to defend it — is a core design decision.

---

## 5. Hard Constraints

- **Persistence.** Data survives `docker compose down && docker compose up`. Use a named Docker volume.
- **Concurrent sessions.** Multiple `session_id`s active at once must not bleed unless your design intentionally shares cross-session knowledge for the same `user_id` — and that decision must be in the README.
- **Synchronous correctness.** After `POST /turns` returns, the ingested data and extracted memories must be immediately available via `/recall` and `/users/{user_id}/memories`. No eventual consistency — if you wrote it, you can read it.
- **Recall budget.** `POST /recall` should return within a reasonable time. If your extraction or retrieval pipeline is slow, document why and what you'd optimize.
- **Resilience.** Service must not crash on malformed input, oversized payloads, or unicode oddities.
- **LLM usage is encouraged.** Use any LLM you want for extraction, summarization, or retrieval — OpenAI, Anthropic, local models via Ollama, whatever gets the best results. Document which models you use and why in the README. List required API keys in `.env.example`.

---

## 6. Submission Format

A single Git repository. Required structure:

```
memory-service/
├── README.md              # architecture, backing store, recall strategy, tradeoffs
├── CHANGELOG.md           # iteration history (see below)
├── docker-compose.yml     # boots the service + any deps (db, vector store, etc.)
├── Dockerfile             # the service container
├── src/                   # your code (any language)
├── tests/                 # service-internal tests (see §7)
├── fixtures/              # your self-eval test data
└── .env.example           # any optional env vars
```

---

### README.md (primary deliverable)

Must contain:

1. **Architecture** — diagram (ASCII, Mermaid, or image) + 1–2 paragraphs.
2. **Backing store choice** — what and why.
3. **Extraction pipeline** — how raw turns become structured memories. What do you extract? How? What do you miss and why?
4. **Recall strategy** — how `/recall` works end-to-end. How do you rank? How do you handle the token budget? What's your priority logic when budget is tight?
5. **Fact evolution** — how you handle contradictions, corrections, and opinion changes.
6. **Tradeoffs** — what you optimized for and what you gave up.
7. **Failure modes** — what happens with no data, slow disk, missing API keys.
8. **How to run the tests.**

---

### CHANGELOG.md (most important deliverable)

One entry per significant design iteration. Include what you tried, what you observed, and why you changed direction. Example:

```
## v3 — Hybrid retrieval with reciprocal rank fusion

**What changed:** Added BM25 alongside embedding search and fused results with RRF.

**Why:** Pure embedding search was missing keyword-heavy queries like "what's their
dog's name?" where exact token match matters more than semantic similarity.
Noticed this in my test fixture — queries 4, 7, 11 were all keyword-dependent.

**Result:** Self-eval recall improved from 0.52 to 0.64. Precision stayed flat.
Latency increased ~40ms due to double retrieval — acceptable.

**Next:** Contradiction handling is still broken. "Moved to Berlin" and
"lives in NYC" both come back as active. Need to add supersession logic.
```

The CHANGELOG is how we see your engineering process. A submission with a mediocre final score but a thoughtful 5-entry CHANGELOG beats a higher score with no iteration history.

---

## 7. Testing and Self-Eval

### Required: contract tests

Your `tests/` must include at minimum:

- Contract roundtrip: write a turn, recall it, verify the shape.
- Restart persistence: write turns, restart the service, recall — data survives.
- Concurrent sessions: two sessions with different users don't bleed.
- Malformed input: bad JSON, missing fields, unicode — 4xx, not crash.

---

### Required: recall quality fixture

Ship a small fixture in `fixtures/` (3–5 scripted conversations + probe queries with expected facts). Your tests should ingest the conversations, run the probes against `/recall`, and report a basic quality metric (even if it's just "X of Y expected facts appeared in context").

This is your iteration loop. Candidates who build a quality fixture early and run it after every change produce measurably better submissions.

---

### We provide: a smoke test

To verify your service is compatible with our eval harness before you submit, we provide a minimal smoke test:

```
# after docker compose up
curl-s http://localhost:8080/health | jq .

curl-X POST http://localhost:8080/turns \
-H'Content-Type: application/json' \
-d'{
    "session_id": "smoke-1",
    "user_id": "user-1",
    "messages": [
      {"role": "user", "content": "I just moved to Berlin from NYC last month. Loving it so far."},
      {"role": "assistant", "content": "That sounds exciting! Berlin is a great city. How are you settling in?"}
    ],
    "timestamp": "2025-03-15T10:30:00Z",
    "metadata": {}
  }'

curl-X POST http://localhost:8080/recall \
-H'Content-Type: application/json' \
-d'{
    "query": "Where does this user live?",
    "session_id": "smoke-2",
    "user_id": "user-1",
    "max_tokens": 512
  }'
# should mention Berlin, ideally note the move from NYC

curl http://localhost:8080/users/user-1/memories | jq .
# should show structured memories, not raw message text
```

If the smoke test works and the shapes match, your service will run against our eval.

---

## 8. Setup We'll Use

Exactly this — make sure it works on a clean machine:

```
git clone <your repo> memory-service
cd memory-service
docker compose up-d
# wait for health
untilcurl-sf http://localhost:8080/health;dosleep1;done
# our eval now points at http://localhost:8080
```

The default port should be `8080` (or you document the override clearly). The service should expose its port via `docker-compose.yml`. No manual `pip install`, no `npm run setup`. If your service requires API keys, document them clearly in `.env.example` — we'll provide them when we run the eval.

---

## 9. How It Will Be Tested

Two grading channels:

---

### A. Automated private eval — measures memory behavior

We `docker compose up` your service and run a private eval harness that exercises the contract endpoints across scripted multi-session conversations. You will not see fixtures, thresholds, or the judge prompts. You **will** be told the categories:

- **Recall quality.** Does `/recall` surface the facts a follow-up question depends on? Scored by a held-out QA set. **Primary signal.**
- **Fact evolution.** A user states "I work at Stripe" in session 1 and "I just joined Notion" in session 3. Does `/recall` return the current fact? Does it still know the history? Does `/users/{user_id}/memories` show the supersession chain?
- **Multi-hop recall.** Questions where naive top-k fails. "What city does the user with the dog named Biscuit live in?" requires connecting two separate memories.
- **Noise resistance.** Queries about topics never discussed. Service should return empty context, not hallucinated memories.
- **Extraction quality.** We inspect `/users/{user_id}/memories` after ingesting conversations. Are memories structured and typed? Are implicit facts captured? Are corrections handled?
- **Persistence across restarts.** Facts written before `docker compose down` are recallable after `docker compose up`.
- **Cross-session scoping.** Concurrent sessions don't bleed unless your design intentionally shares for the same user — and that's documented.
- **Robustness.** Survives malformed input, missing auth, slow/empty store, restart mid-write. Service stays up; errors are 4xx/5xx, not crashes.
- **Correctness.** After `/turns` returns, extracted memories are immediately available via `/recall`. No eventual consistency gaps.
- **Contract compliance.** Endpoints exist, shapes match, status codes are correct.

---

### B. Human architecture review — measures design

We read your code, your tests, your CHANGELOG, your README. We judge:

- Is the architecture sound? Is the backing store choice justified?
- Is the extraction pipeline real, or are "memories" just message chunks?
- Is the recall pipeline thoughtful, or a vanilla shortcut?
- Does the CHANGELOG show genuine iteration and learning?
- Is the code clean, well-tested, well-logged?
- Could a maintainer extend this in 6 months?

Be ready to defend every design choice in a 30-minute follow-up interview.

---

## 10. What "Excellent" Looks Like

- Contract compliance is exact — endpoints, shapes, status codes.
- Extraction produces structured memories with types, confidence, and provenance — not raw text blobs.
- Fact evolution works: contradictions detected, old facts superseded, history preserved.
- Real recall ranking — embedding + reranking, hybrid, graph, multi-hop, query rewriting — something deliberate. Vanilla cosine-top-k will not score.
- Context assembly has explicit priority logic under token budget. Defended in README.
- `/turns` is synchronous — after it returns, memories are immediately queryable. No race conditions, no eventual consistency.
- `/recall` returns well-formatted context within the token budget.
- Persistence is real — restart is invisible to clients.
- Service degrades gracefully under failure (no crashes, sensible errors).
- Tests cover: contract roundtrip, restart persistence, concurrent sessions, malformed input, recall quality on a self-built fixture.
- CHANGELOG has 4+ entries showing clear iteration with metrics at each step.
- README walks a reviewer to understanding the design in 5 minutes.
- `/users/{user_id}/memories` shows a clean, inspectable memory store.

---

## 11. Originality Rule

You can read public memory-system designs (mem0, hindsight, honcho, mnemonic agents, etc.) for inspiration. **Your submission must be your own design.** Don't lift another project's API shape or recall pipeline. If your work closely resembles an existing system, expect to defend the resemblance line-by-line in the follow-up interview.

We're hiring people who can design memory systems, not people who can rename files.

---

## 12. Out of Scope

- No agent-side code. We handle that.
- No UI.
- No multi-tenant production-readiness. One service, a few concurrent sessions, single user — fine.
- No horizontal scalability proofs. If your design happens to be horizontally scalable, mention it; don't build for it.
- No migration story. Single schema version is fine.

---

## 13. Questions

If a question materially blocks progress, ask. Otherwise, derive it from §3 and design freely.

Good luck.