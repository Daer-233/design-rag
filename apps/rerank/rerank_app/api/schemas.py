from pydantic import BaseModel, Field


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16_000)
    documents: list[str] = Field(min_length=1, max_length=100)


class RerankItem(BaseModel):
    index: int
    score: float


class RerankResponse(BaseModel):
    model_id: str
    model_revision: str
    model_weights_sha256: str | None
    results: list[RerankItem]
