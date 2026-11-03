import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from rerank_app.api.schemas import RerankItem, RerankRequest, RerankResponse
from rerank_app.core.config import settings
from rerank_app.services.bge_reranker import ModelNotReadyError

router = APIRouter(tags=["rerank"])


def model_weights_sha256() -> str | None:
    manifest_path = Path(settings.model_dir) / "design-rag-model-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8")).get("model_weights_sha256")
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) else None


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok" if request.app.state.reranker.is_ready else "not_ready",
        "service": settings.service_name,
        "model_id": settings.model_id,
        "model_revision": settings.model_revision,
        "model_weights_sha256": model_weights_sha256(),
        "device": "cpu",
    }


@router.post("/rerank", response_model=RerankResponse)
async def rerank(payload: RerankRequest, request: Request) -> RerankResponse:
    try:
        scores = await asyncio.to_thread(request.app.state.reranker.score, payload.query, payload.documents)
    except ModelNotReadyError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reranker inference failed: {type(error).__name__}: {error}",
        ) from error

    ranked = sorted(
        (RerankItem(index=index, score=score) for index, score in enumerate(scores)),
        key=lambda item: item.score,
        reverse=True,
    )
    return RerankResponse(
        model_id=settings.model_id,
        model_revision=settings.model_revision,
        model_weights_sha256=model_weights_sha256(),
        results=ranked,
    )
