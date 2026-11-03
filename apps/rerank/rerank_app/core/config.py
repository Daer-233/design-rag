"""Configuration for the CPU-local BGE reranker service."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = Field("design-rag-rerank", validation_alias="RERANK_APP_NAME")
    host: str = Field("127.0.0.1", validation_alias="RERANK_HOST")
    port: int = Field(8032, validation_alias="RERANK_PORT")
    model_id: str = Field("BAAI/bge-reranker-v2-m3", validation_alias="RERANK_MODEL_ID")
    model_revision: str = Field("master", validation_alias="RERANK_MODEL_REVISION")
    model_dir: Path = Field(
        Path(r"I:\design-ai-data\models\bge-reranker-v2-m3"),
        validation_alias="RERANK_MODEL_DIR",
    )
    batch_size: int = Field(1, ge=1, le=32, validation_alias="RERANK_BATCH_SIZE")
    max_length: int = Field(512, ge=128, le=1024, validation_alias="RERANK_MAX_LENGTH")
    eager_load: bool = Field(False, validation_alias="RERANK_EAGER_LOAD")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()


