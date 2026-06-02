#!/usr/bin/env sh
# Entrypoint: run Alembic migrations, then hand off to uvicorn.
# Postgres readiness is guaranteed by docker-compose `depends_on: condition: service_healthy`.
set -e

echo "[entrypoint] Running: alembic upgrade head"
alembic upgrade head
echo "[entrypoint] Migrations applied."

exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level info
