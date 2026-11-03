"""Local CPU BGE-Reranker-v2-m3 adapter."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class ModelNotReadyError(RuntimeError):
    pass


class BGEReranker:
    def __init__(self, *, model_dir: Path, batch_size: int, max_length: int) -> None:
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
                    f"Reranker model directory does not exist: {self._model_dir}. "
                    "Run scripts/download-bge-reranker-v2-m3.ps1 first."
                )
            try:
                from FlagEmbedding import FlagReranker
            except ImportError as error:  # pragma: no cover
                raise ModelNotReadyError("FlagEmbedding is not installed.") from error
            self._model = FlagReranker(
                str(self._model_dir),
                use_fp16=False,
                devices=["cpu"],
            )

    def score(self, query: str, documents: list[str]) -> list[float]:
        self.load()
        assert self._model is not None
        pairs = [(query, document) for document in documents]
        scores = self._model.compute_score(
            pairs,
            batch_size=self._batch_size,
            max_length=self._max_length,
        )
        if isinstance(scores, float):
            return [scores]
        return [float(score) for score in scores]
