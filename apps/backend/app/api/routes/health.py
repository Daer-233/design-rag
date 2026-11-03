"""Health and readiness endpoints."""

from fastapi import APIRouter

from app.clients.inference import EmbeddingClient, InferenceServiceError, RerankClient
from app.core.config import settings
from app.db.connection import check_postgres
from app.providers.vector.factory import get_vector_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "ports": {
            "backend": settings.app_port,
            "embedding": 8031,
            "rerank": 8032,
        },
    }


@router.get("/ready")
def ready() -> tuple[dict[str, object], int] | dict[str, object]:
    try:
        embedding = EmbeddingClient().health()
        embedding_ok = embedding.get("status") == "ok"
    except InferenceServiceError:
        embedding_ok = False
    try:
        rerank = RerankClient().health()
        rerank_ok = rerank.get("status") == "ok"
    except InferenceServiceError:
        rerank_ok = False
    checks = {
        "postgres": check_postgres(),
        "qdrant": get_vector_provider().health(),
        "embedding": embedding_ok,
        "rerank": rerank_ok,
    }
    return {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks}
