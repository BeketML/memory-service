from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.storage.postgres.database import check_healthy as pg_healthy
from src.storage.qdrant.collection import check_healthy as qdrant_healthy

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    pg_ok = await pg_healthy()
    qd_ok = await qdrant_healthy()

    if pg_ok and qd_ok:
        return JSONResponse(status_code=200, content={"status": "ok"})
    return JSONResponse(
        status_code=503,
        content={
            "status": "degraded",
            "postgres": pg_ok,
            "qdrant": qd_ok,
        },
    )
