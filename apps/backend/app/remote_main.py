"""Minimal network-facing Design_RAG application for remote evidence retrieval.

Run this application (not `app.main:app`) on a LAN or reverse-proxy listener. It
intentionally exposes only `/health` and authenticated `/api/v1/search`; ingestion,
PostgreSQL administration, Qdrant, source files, and `/api/v1/answer` are absent.
"""

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.search import router as search_router
from app.core.config import settings

app = FastAPI(
    title=f"{settings.app_name} remote search",
    version="0.1.0",
    description="Authenticated read-only evidence retrieval for remote OpenClaw clients",
)
app.include_router(health_router)
app.include_router(search_router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": f"{settings.app_name}-remote-search",
        "status": "ok",
        "docs": "/docs",
    }
