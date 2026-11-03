"""Configuration for the CPU-local BGE-M3 embedding service."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = Field("design-rag-embedding", validation_alias="EMBEDDING_APP_NAME")
    app_env: str = "development"
    host: str = Field("127.0.0.1", validation_alias="EMBEDDING_HOST")
    port: int = Field(8031, validation_alias="EMBEDDING_PORT")
    log_level: str = "INFO"

    model_id: str = Field("BAAI/bge-m3", validation_alias="EMBEDDING_MODEL_ID")
    model_revision: str | None = Field("master", validation_alias="EMBEDDING_MODEL_REVISION")
    model_dir: Path = Field(
        Path(r"I:\design-ai-data\models\bge-m3"),
        validation_alias="EMBEDDING_MODEL_DIR",
    )
    batch_size: int = Field(4, validation_alias="EMBEDDING_BATCH_SIZE")
    max_length: int = Field(8192, validation_alias="EMBEDDING_MAX_LENGTH")
    eager_load: bool = Field(False, validation_alias="EMBEDDING_EAGER_LOAD")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
