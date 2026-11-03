"""HTTP clients for locally hosted embedding and rerank services."""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


class InferenceServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelIdentity:
    kind: str
    provider: str
    model_id: str
    source_revision: str
    artifact_sha256: str | None
    config: dict[str, object]


class EmbeddingClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.embedding_service_url).rstrip("/")

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def model_identity(self) -> ModelIdentity:
        try:
            health = self.health()
            return ModelIdentity(
                kind="embedding",
                provider="modelscope",
                model_id=str(health.get("model_id") or settings.embedding_model_id),
                source_revision=str(health.get("model_revision") or settings.embedding_model_revision),
                artifact_sha256=health.get("pytorch_weights_sha256"),
                config={
                    "service_url": self.base_url,
                    "device": health.get("device", "cpu"),
                    "dense": health.get("dense", True),
                    "sparse": health.get("sparse", True),
                },
            )
        except InferenceServiceError:
            # Persist enough information to rebuild once the local service returns.
            return ModelIdentity(
                kind="embedding",
                provider="modelscope",
                model_id=settings.embedding_model_id,
                source_revision=settings.embedding_model_revision,
                artifact_sha256=None,
                config={"service_url": self.base_url, "identity_source": "backend_configuration"},
            )

    def embed(self, texts: list[str]) -> dict[str, Any]:
        data = self._post("/embed", {"texts": texts})
        if len(data.get("dense_vectors", [])) != len(texts) or len(data.get("sparse_vectors", [])) != len(texts):
            raise InferenceServiceError("Embedding service returned an incomplete vector batch")
        return data

    @staticmethod
    def _request_batches(
        texts: list[str], *, batch_size: int, long_text_chars: int
    ) -> list[list[str]]:
        """Keep long parent chunks out of the same CPU inference request.

        BGE-M3's CPU cost rises sharply with sequence length. Parent chunks are
        intentionally larger than retrieval children, so a single parent plus
        multiple children can exceed the HTTP timeout even though every chunk is
        valid. Long chunks therefore receive their own request; child chunks keep
        the configured batch size. Input order is retained for vector/chunk mapping.
        """
        batches: list[list[str]] = []
        pending: list[str] = []
        for text in texts:
            if len(text) > long_text_chars:
                if pending:
                    batches.append(pending)
                    pending = []
                batches.append([text])
                continue
            pending.append(text)
            if len(pending) == batch_size:
                batches.append(pending)
                pending = []
        if pending:
            batches.append(pending)
        return batches

    def embed_many(self, texts: list[str], batch_size: int | None = None) -> dict[str, Any]:
        """Embed locally, isolating long parent contexts from child-sized batches."""
        effective_batch_size = batch_size or settings.embedding_request_batch_size
        if not texts:
            return {"dense_vectors": [], "sparse_vectors": [], "dense_dimension": 0}
        batches = [
            self.embed(batch)
            for batch in self._request_batches(
                texts,
                batch_size=effective_batch_size,
                long_text_chars=settings.embedding_long_text_batch_chars,
            )
        ]
        dimensions = {int(batch["dense_dimension"]) for batch in batches}
        if len(dimensions) != 1:
            raise InferenceServiceError("Embedding dimension changed within one ingestion request")
        return {
            "dense_dimension": dimensions.pop(),
            "dense_vectors": [vector for batch in batches for vector in batch["dense_vectors"]],
            "sparse_vectors": [vector for batch in batches for vector in batch["sparse_vectors"]],
        }

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
                response = client.get(f"{self.base_url}{path}")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise InferenceServiceError(f"Embedding service unavailable: {type(error).__name__}") from error

    def _post(self, path: str, body: dict[str, object]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
                response = client.post(f"{self.base_url}{path}", json=body)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise InferenceServiceError(f"Embedding service request failed: {type(error).__name__}") from error


class RerankClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.rerank_service_url).rstrip("/")

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
                response = client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise InferenceServiceError(f"Rerank service unavailable: {type(error).__name__}") from error

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, float | int]]:
        try:
            with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/rerank", json={"query": query, "documents": documents}
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise InferenceServiceError(f"Rerank service request failed: {type(error).__name__}") from error
        return [
            {"index": int(item["index"]), "score": float(item["score"])}
            for item in data.get("results", [])
        ]

