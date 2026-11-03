"""Application configuration."""

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "design-rag"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8030
    log_level: str = "INFO"

    postgres_dsn: str = "postgresql://design_rag:REPLACE_ME@127.0.0.1:5432/design_rag"
    qdrant_url: str = "http://127.0.0.1:6333"
    vector_provider: str = "qdrant"
    qdrant_collection: str = "design_rag_chunks_v1"

    # Agent evidence-only API. Tokens and allowed projects are configured server-side.
    # DESIGN_RAG_API_TOKEN remains a single-token compatibility setting.
    design_rag_api_token: str | None = None
    # Short-lived signed Chat image references; never sent to browsers as API credentials.
    design_rag_asset_signing_secret: str | None = None
    design_rag_api_principal_user_id: str = "readonly-agent"
    design_rag_api_allowed_project_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    design_rag_api_principal_roles: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # JSON object keyed by token. Example:
    # {"token-a":{"agent_id":"agent-a","project_ids":["<uuid>"],"roles":["designer"]}}
    design_rag_agent_tokens: Annotated[str | None, NoDecode] = None

    # Local Chat Agent knowledge access goes through the Node.js MCP gateway.
    design_rag_mcp_enabled: bool = True
    design_rag_mcp_url: str = "http://127.0.0.1:8040/mcp"
    design_rag_mcp_timeout_seconds: float = Field(default=360.0, gt=0, le=600)


    embedding_service_url: str = "http://127.0.0.1:8031"
    rerank_service_url: str = "http://127.0.0.1:8032"
    embedding_model_id: str = "BAAI/bge-m3"
    embedding_model_revision: str = "master"

    retrieval_recall_top_k: int = Field(default=100, ge=1, le=500)
    retrieval_coarse_top_k: int = Field(default=40, ge=1, le=200)
    # Legacy name remains an input-budget fallback for existing deployments.
    retrieval_rerank_top_k: int = Field(default=8, ge=1, le=100)
    retrieval_rerank_candidate_top_k: int | None = Field(default=None, ge=1, le=100)
    retrieval_evidence_top_k: int = Field(default=8, ge=1, le=50)
    # Keep one document from dominating an answer when many sibling chunks match.
    retrieval_max_evidence_per_document: int = Field(default=2, ge=1, le=20)
    # Raw BGE reranker logit, not a calibrated probability; manual-instance only.
    knowledge_rerank_min_score: float = Field(default=0.0, ge=-50, le=50)

    @property
    def effective_rerank_candidate_top_k(self) -> int:
        if self.retrieval_rerank_candidate_top_k is not None:
            return self.retrieval_rerank_candidate_top_k
        return self.retrieval_rerank_top_k

    # CPU BGE-M3 needs a longer allowance for parent-context chunks; individual long
    # chunks are sent separately by EmbeddingClient to avoid a whole batch timing out.
    inference_timeout_seconds: float = Field(default=360.0, gt=0)
    embedding_request_batch_size: int = Field(default=4, ge=1, le=64)
    embedding_long_text_batch_chars: int = Field(default=1_800, ge=256, le=20_000)
    data_root: Path = Path(r"I:\design-ai-data")

    # OpenAI-compatible LLM gateway. It is independent of local Embedding/Rerank.
    # Actual external submission remains an explicit Step-6 action.
    llm_base_url: str = "https://nowcoding.ai/v1"
    llm_api_key: str | None = None
    # Backward-compatible fallback for an existing private gateway key.
    openai_api_key: str | None = None
    llm_model_id: str = "gpt-5.6-terra"
    # Sent only to OpenAI-compatible gateways that support GPT reasoning controls.
    # Keep it low for responsive, cost-conscious evidence-grounded answers.
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] = "low"
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    # Generation is intentionally disabled in Agent-retrieval mode. It can only be
    # re-enabled explicitly for a separately protected local workflow.
    answer_api_enabled: bool = False
    answer_prompt_version: str = "grounded-answer-v1"
    answer_max_tokens: int = Field(default=1600, ge=128, le=8000)
    image_generation_adapter_command: str | None = None
    image_generation_poll_interval_seconds: float = Field(default=5.0, ge=1.0, le=300.0)
    answer_evidence_child_max_chars: int = Field(default=3000, ge=256, le=20000)
    answer_evidence_parent_max_chars: int = Field(default=5000, ge=256, le=30000)
    answer_evidence_total_max_chars: int = Field(default=30000, ge=1000, le=100000)
    llm_vision_prompt_version: str = "design-image-v2-materials"
    llm_vision_batch_max_images: int = Field(default=20, ge=1, le=100)
    # Only EXIF-free, locally derived images may leave the machine for vision analysis.
    vision_analysis_derivative_root: Path = Path(r"I:\design-ai-data\vision-analysis-derivatives")
    vision_analysis_max_dimension: int = Field(default=2048, ge=256, le=8192)
    # Local-only document page rendering. No page leaves the host until Admin approves Vision.
    document_page_vision_enabled: bool = True
    document_page_vision_dpi: int = Field(default=144, ge=72, le=300)
    document_page_vision_max_pages: int = Field(default=300, ge=1, le=2000)
    document_page_render_timeout_seconds: float = Field(default=300.0, gt=0, le=1800)
    soffice_path: Path = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    pdftoppm_path: Path = Path(r"C:\Users\liwj\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe")

    @property
    def effective_llm_api_key(self) -> str | None:
        return self.llm_api_key or self.openai_api_key

    # Anonymous local Agent. Authentication and external network deployment are deliberately
    # deferred; browser isolation uses an HttpOnly anonymous-session cookie in this phase.
    agent_enabled: bool = True
    agent_cookie_name: str = "design_rag_anonymous_id"
    agent_memory_message_limit: int = Field(default=16, ge=4, le=100)
    # Fast local polling lets persisted SSE events appear while generation is still in progress.
    agent_event_poll_interval_seconds: float = Field(default=0.1, ge=0.05, le=5.0)
    agent_event_heartbeat_seconds: float = Field(default=15.0, ge=5.0, le=60.0)
    agent_stream_chunk_chars: int = Field(default=36, ge=8, le=512)
    # The workflow stores a completed answer first, then emits replayable visible chunks.
    # A small interval prevents React from batching all chunks into one paint.
    agent_stream_chunk_interval_seconds: float = Field(default=0.08, ge=0.0, le=2.0)
    agent_evidence_top_k: int = Field(default=6, ge=1, le=50)
    # ModelScope only: this Chinese RoBERTa/BERT NLI checkpoint performs the initial
    # professional-vs-daily routing with two explicit natural-language hypotheses.
    agent_intent_model_id: str = "Fengshenbang/Erlangshen-RoBERTa-110M-NLI"
    agent_intent_model_revision: str = "v1.0.0"
    # After the explicit ModelScope download, inference must remain local and must not request Hugging Face or ModelScope.
    agent_intent_local_files_only: bool = True
    agent_intent_max_length: int = Field(default=256, ge=64, le=512)
    # MinerU Precision API v4. MINERU_API_TOKEN remains the only mandatory setting.
    mineru_api_token: str | None = None
    mineru_api_base_url: str = "https://mineru.net/api/v4"
    mineru_submit_url: str | None = None
    mineru_status_url_template: str | None = None
    mineru_result_url_template: str | None = None
    # MinerU Precision API: vlm is the recommended cloud model profile.
    mineru_model_version: str = "vlm"
    mineru_precision_max_file_bytes: int = Field(default=209_715_200, ge=1, le=1_073_741_824)
    mineru_enable_formula: bool = True
    mineru_enable_table: bool = True
    mineru_language: str = "ch"
    mineru_poll_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # The local full backend reconciles only already-authorized MinerU jobs.  This
    # keeps the Admin page current without creating any additional external work.
    ingestion_reconcile_interval_seconds: float = Field(default=15.0, ge=5.0, le=3600.0)
    ingestion_reconcile_batch_size: int = Field(default=20, ge=1, le=200)
    # Import schedules only dispatch task kinds the Admin user explicitly approved.
    import_batch_schedule_reconcile_interval_seconds: float = Field(default=15.0, ge=1.0, le=3600.0)
    import_batch_nightly_hour: int = Field(default=19, ge=0, le=23)
    # The vision worker consumes only entries that were explicitly placed in its local
    # queue. It never discovers or exports images on its own.
    visual_analysis_reconcile_interval_seconds: float = Field(default=3.0, ge=1.0, le=3600.0)
    visual_analysis_reconcile_batch_size: int = Field(default=5, ge=1, le=20)
    # A local process restart can leave a claimed job in ``running`` forever.  The
    # worker only repairs records older than this conservative threshold; it never
    # silently sends the image to the external model again.
    visual_analysis_stale_running_seconds: int = Field(default=3600, ge=300, le=7 * 24 * 3600)
    # RAGAS runs are queued by Admin and processed by the local backend only.
    rag_evaluation_reconcile_interval_seconds: float = Field(default=3.0, ge=1.0, le=3600.0)
    rag_evaluation_max_cases_per_run: int = Field(default=30, ge=1, le=100)
    # Project material-consistency audits are explicitly queued by local Admin and recovered after restart.
    material_consistency_reconcile_interval_seconds: float = Field(default=2.0, ge=0.5, le=3600.0)
    material_consistency_prompt_version: str = "material-consistency-v1"
    mineru_supported_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "pdf", "png", "jpg", "jpeg", "jp2", "webp", "gif", "bmp", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "html"
        ]
    )
    # Kept broader than MinerU's direct formats so unsupported files can still be
    # archived and routed to a future pre-conversion/parser flow without external export.
    ingestion_allowed_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "png", "jpg", "jpeg", "webp"]
    )
    # Must exceed MinerU's 200 MiB Precision API limit so an oversized design image
    # can be archived and deliberately routed to GPT vision instead of being rejected at upload.
    ingestion_max_upload_bytes: int = Field(default=536_870_912, ge=1, le=1_073_741_824)

    @field_validator(
        "ingestion_allowed_extensions",
        "mineru_supported_extensions",
        "design_rag_api_allowed_project_ids",
        "design_rag_api_principal_roles",
        mode="before",
    )
    @classmethod
    def split_extensions(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).lower().lstrip(".") for item in value]
        return []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings(_env_file=os.environ.get("DESIGN_RAG_ENV_FILE", ".env"))
