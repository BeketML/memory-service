FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the two LOCAL embedding models (BM25 + ColBERT) so first
# startup is fast.  Dense embeddings are served by the OpenAI API and need
# no local download.
RUN python -c "\
import os; \
os.makedirs('/app/.cache/fastembed', exist_ok=True); \
from fastembed import SparseTextEmbedding, LateInteractionTextEmbedding; \
print('Downloading BM25 sparse model...'); \
list(SparseTextEmbedding('Qdrant/bm25').embed(['warmup'])); \
print('Downloading colbert-ir/colbertv2.0...'); \
list(LateInteractionTextEmbedding('colbert-ir/colbertv2.0').embed(['warmup'])); \
print('Local embedding models ready.'); \
"

# Copy application source and Alembic migrations
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini .

# Copy and wire up the entrypoint (runs migrations before uvicorn)
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
