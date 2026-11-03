"""Provider-neutral vector search contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorRecord:
    point_id: str
    chunk_id: str
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]
    payload: dict[str, object]


@dataclass(frozen=True)
class SearchFilter:
    project_id: str | None
    principal_roles: list[str]
    chunk_level: str = "child"
    file_types: list[str] | None = None
    chunk_types: list[str] | None = None
    content_origins: list[str] | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class VectorCandidate:
    chunk_id: str
    dense_score: float | None
    dense_rank: int | None
    sparse_score: float | None
    sparse_rank: int | None
    fusion_score: float


class VectorProvider(Protocol):
    provider_name: str

    def ensure_collection(self, dense_dimension: int) -> None: ...

    def upsert_chunks(self, records: list[VectorRecord], dense_dimension: int) -> None: ...

    def hybrid_search(
        self,
        *,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        search_filter: SearchFilter,
        limit: int,
    ) -> list[VectorCandidate]: ...

    def health(self) -> bool: ...
