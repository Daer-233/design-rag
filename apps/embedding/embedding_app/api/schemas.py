"""Request and response schemas for embedding."""

from pydantic import BaseModel, Field


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


class SparseVector(BaseModel):
    indices: list[int]
    values: list[float]


class EmbedResponse(BaseModel):
    model_id: str
    model_revision: str
    pytorch_weights_sha256: str | None
    dense_dimension: int
    dense_vectors: list[list[float]]
    sparse_vectors: list[SparseVector]
