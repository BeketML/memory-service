src/
├── main.py                      # FastAPI app, lifespan, mount routers
├── config.py                    # pydantic-settings: PG, Qdrant, LLM, auth, models
│
├── api/                         # HTTP-контракт (§3 task.md)
│   ├── deps.py                  # DB session, clients, optional Bearer auth
│   ├── middleware/
│   │   └── auth.py
│   └── routes/
│       ├── health.py            # PG + Qdrant + collection
│       ├── turns.py
│       ├── recall.py
│       ├── search.py
│       ├── memories.py          # GET /users/{user_id}/memories
│       ├── sessions.py          # DELETE /sessions/{session_id}
│       ├── users.py             # DELETE /users/{user_id}
│       └── admin.py             # опционально: POST /admin/reindex
│
├── schemas/                     # Pydantic: request/response по контракту
│   ├── turns.py
│   ├── recall.py
│   ├── search.py
│   └── memories.py
│
├── services/                    # оркестрация use-case’ов (sequence из §8 design)
│   ├── ingest.py                # POST /turns: persist → extract → reconcile → embed → Qdrant
│   ├── recall.py                # POST /recall: tier1 PG → tier2 hybrid → tier3 → assemble
│   ├── search.py                # POST /search: только tier2, плоский список
│   └── delete.py                # cleanup session/user + Qdrant filters
│
├── extraction/                  # «не просто storage» (§4 task, §5 design)
│   ├── llm.py                   # вызов провайдера
│   ├── prompts.py
│   └── parser.py                # JSON candidates: type, key, value, operation, …
│
├── evolution/                   # fact evolution (§6 design)
│   └── reconcile.py             # supersede / bump confidence / opinion arc
│
├── retrieval/                   # hybrid + rerank (§4, §7 design)
│   ├── embedder.py              # BGE-M3: dense + sparse + colbert
│   ├── qdrant.py                # upsert, hybrid query (RRF → ColBERT), filters
│   └── query_rewrite.py         # опциональный LLM для multi-hop
│
├── assembly/                    # приоритеты и бюджет токенов (§7–8 design)
│   ├── tiers.py                 # tier1 facts, tier2 ranked, tier3 recent
│   ├── budget.py                # tiktoken / heuristic
│   └── formatter.py             # markdown «Known facts…», citations
│
└── storage/                     # два хранилища — раздельно
    ├── postgres/
    │   ├── pool.py              # asyncpg / SQLAlchemy engine
    │   ├── models.py            # ORM или dataclasses под таблицы
    │   └── repos/
    │       ├── users.py
    │       ├── sessions.py
    │       ├── turns.py
    │       └── memories.py
    └── qdrant/
        └── collection.py        # create collection, payload indexes