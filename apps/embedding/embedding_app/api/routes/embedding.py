"""Embedding API endpoints."""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from embedding_app.api.schemas import EmbedRequest, EmbedResponse, SparseVector
from embedding_app.core.config import settings
from embedding_app.services.bge_m3 import ModelNotReadyError

router = APIRouter(tags=["embedding"])


def pytorch_weights_sha256() -> str | None:
    manifest_path = Path(settings.model_dir) / "design-rag-model-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = manifest.get("pytorch_weights_sha256")
    return value if isinstance(value, str) else None


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    embedder = request.app.state.embedder
    return {
        "status": "ok" if embedder.is_ready else "not_ready",
        "service": settings.service_name,
        "model_id": settings.model_id,
        "model_revision": settings.model_revision,
        "pytorch_weights_sha256": pytorch_weights_sha256(),
        "model_dir": str(settings.model_dir),
        "device": "cpu",
        "dense": True,
        "sparse": True,
    }


@router.post("/embed", response_model=EmbedResponse)
async def embed(payload: EmbedRequest, request: Request) -> EmbedResponse:
    embedder = request.app.state.embedder
    try:
        result = await asyncio.to_thread(embedder.embed, payload.texts)
    except ModelNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    dense_dimension = len(result.dense_vectors[0]) if result.dense_vectors else 0
    return EmbedResponse(
        model_id=settings.model_id,
        model_revision=settings.model_revision or "master",
        pytorch_weights_sha256=pytorch_weights_sha256(),
        dense_dimension=dense_dimension,
        dense_vectors=result.dense_vectors,
        sparse_vectors=[SparseVector.model_validate(item) for item in result.sparse_vectors],
    )
