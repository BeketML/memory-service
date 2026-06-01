from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL
    database_url: str = "postgresql://memory:memory@localhost:5432/memory"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "memories"

    # LLM extraction
    llm_provider: str = "openai"       # openai | anthropic | ollama
    llm_model: str = "gpt-4o-mini"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    ollama_host: str = "http://localhost:11434"

    # Optional bearer auth
    memory_auth_token: Optional[str] = None

    # Embedding
    embedding_model: str = "BAAI/bge-m3"

    # Retrieval knobs
    max_candidates: int = 50           # per-leg (dense / sparse) before RRF
    rerank_limit: int = 20             # after RRF, before ColBERT rerank
    final_limit: int = 10              # results returned to assembly
    relevance_floor: float = 0.3       # drop Tier-2 results below this score
    tier1_budget_fraction: float = 0.5 # max fraction of max_tokens for stable facts


settings = Settings()
