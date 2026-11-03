from contextlib import asynccontextmanager

from fastapi import FastAPI

from rerank_app.api.router import api_router
from rerank_app.core.config import settings
from rerank_app.services.bge_reranker import BGEReranker, ModelNotReadyError


@asynccontextmanager
async def lifespan(app: FastAPI):
    reranker = BGEReranker(
        model_dir=settings.model_dir,
        batch_size=settings.batch_size,
        max_length=settings.max_length,
    )
    app.state.reranker = reranker
    if settings.eager_load:
        try:
            reranker.load()
        except ModelNotReadyError:
            pass
    yield


app = FastAPI(
    title=settings.service_name,
    version="0.1.0",
    description="CPU-local BGE-Reranker-v2-m3 service",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "status": "ok", "docs": "/docs"}
