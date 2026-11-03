"""Vector provider factory. Business services only depend on the shared contract."""

from functools import lru_cache

from app.core.config import settings
from app.providers.vector.base import VectorProvider
from app.providers.vector.qdrant_provider import QdrantVectorProvider


@lru_cache
def get_vector_provider() -> VectorProvider:
    if settings.vector_provider.lower() == "qdrant":
        return QdrantVectorProvider()
    raise ValueError(f"Unsupported VECTOR_PROVIDER: {settings.vector_provider}")
