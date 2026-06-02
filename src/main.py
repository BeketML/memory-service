from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.middleware.auth import BearerAuthMiddleware
from src.api.routes import admin, health, memories, recall, search, sessions, turns, users
from src.config import settings
from src.retrieval.embedder import load_models
from src.storage.postgres.database import close_db, init_db
from src.storage.qdrant.collection import close_qdrant, init_qdrant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Memory Service...")
    await init_db(settings.database_url)
    logger.info("PostgreSQL ready")
    await init_qdrant()
    logger.info("Qdrant ready")
    await load_models()
    logger.info("BGE-M3 embedding models loaded")
    logger.info("Memory Service ready on port 8080")
    yield
    logger.info("Shutting down...")
    await close_qdrant()
    await close_db()


app = FastAPI(
    title="Memory Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(BearerAuthMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Validation error", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Delegate to specific handlers rather than swallowing them into 500.
    # The generic Exception handler is called by Starlette before more-specific
    # handlers in some middleware orderings, so we route explicitly here.
    if isinstance(exc, RequestValidationError):
        return await validation_exception_handler(request, exc)
    if isinstance(exc, HTTPException):
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


app.include_router(health.router)
app.include_router(turns.router)
app.include_router(recall.router)
app.include_router(search.router)
app.include_router(memories.router)
app.include_router(sessions.router)
app.include_router(users.router)
app.include_router(admin.router)
