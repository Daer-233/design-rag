"""BGE-M3 inference adapter.

The service only loads a local model directory. The model artifact must be fetched by
ModelScope before this process starts; no model download is attempted at request time.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

class ModelNotReadyError(RuntimeError):
    """Raised when the local embedding model is unavailable."""


@dataclass(frozen=True)
class EmbeddingResult:
    dense_vectors: list[list[float]]
    sparse_vectors: list[dict[str, list[float] | list[int]]]


class BGEM3Embedder:
    def __init__(
        self,
        *,
        model_dir: Path,
        batch_size: int,
        max_length: int,
    ) -> None:
        self._model_dir = model_dir
        self._batch_size = batch_size
        self._max_length = max_length
        self._model: Any | None = None
        self._load_lock = Lock()

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return

        with self._load_lock:
            if self._model is not None:
                return
            if not self._model_dir.is_dir():
                raise ModelNotReadyError(
                    f"BGE-M3 model directory does not exist: {self._model_dir}. "
                    "Run scripts/download-bge-m3.ps1 first."
                )

            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as error:  # pragma: no cover - installation failure
                raise ModelNotReadyError(
                    "FlagEmbedding is not installed. Install requirements/inference.txt."
                ) from error

            self._model = BGEM3FlagModel(
                str(self._model_dir),
                use_fp16=False,
                devices=["cpu"],
            )

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.load()
        assert self._model is not None

        output = self._model.encode(
            texts,
            batch_size=self._batch_size,
            max_length=self._max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        dense_values = output["dense_vecs"]
        dense_vectors = [
            vector.tolist() if hasattr(vector, "tolist") else list(vector) for vector in dense_values
        ]

        lexical_weights = output["lexical_weights"]
        sparse_vectors: list[dict[str, list[float] | list[int]]] = []
        for weights in lexical_weights:
            sparse_vectors.append(
                {
                    "indices": [int(token_id) for token_id in weights.keys()],
                    "values": [float(weight) for weight in weights.values()],
                }
            )

        return EmbeddingResult(dense_vectors=dense_vectors, sparse_vectors=sparse_vectors)
