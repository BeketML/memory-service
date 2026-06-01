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

# Pre-download BGE-M3 models so first startup is fast
RUN python -c "\
import os; \
os.makedirs('/app/.cache/fastembed', exist_ok=True); \
from fastembed import TextEmbedding, SparseTextEmbedding, LateInteractionTextEmbedding; \
print('Downloading BGE-M3 dense...'); \
list(TextEmbedding('BAAI/bge-m3').embed(['warmup'])); \
print('Downloading BGE-M3 sparse...'); \
list(SparseTextEmbedding('BAAI/bge-m3').embed(['warmup'])); \
print('Downloading BGE-M3 colbert...'); \
list(LateInteractionTextEmbedding('BAAI/bge-m3').embed(['warmup'])); \
print('All BGE-M3 models ready.'); \
"

# Copy application source
COPY src/ ./src/

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
