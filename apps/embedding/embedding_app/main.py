"""CPU-local BGE-M3 embedding FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from embedding_app.api.router import api_router
from embedding_app.core.config import settings
from embedding_app.services.bge_m3 import BGEM3Embedder, ModelNotReadyError


@asynccontextmanager
async def lifespan(app: FastAPI):
    embedder = BGEM3Embedder(
        model_dir=settings.model_dir,
        batch_size=settings.batch_size,
        max_length=settings.max_length,
    )
    app.state.embedder = embedder

    if settings.eager_load:
        try:
            embedder.load()
        except ModelNotReadyError:
            # /health and /embed provide the actionable error without preventing /docs from opening.
            pass
    yield


app = FastAPI(
    title=settings.service_name,
    version="0.1.0",
    description="CPU-local BGE-M3 dense and sparse embedding service",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "status": "ok", "docs": "/docs"}
