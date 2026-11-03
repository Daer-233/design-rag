"""Public HTTP schemas for authoritative parent-child chunk ingestion and evidence retrieval."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInput(StrictModel):
    external_key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentInput(StrictModel):
    external_key: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1, max_length=4000)
    file_name: str = Field(min_length=1, max_length=1000)
    file_type: str = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_scope: dict[str, Any] = Field(default_factory=dict)


class ParserInput(StrictModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=200)
    parsed_content_uri: str | None = Field(default=None, max_length=4000)
    parsed_payload: dict[str, Any] = Field(default_factory=dict)
    source_confidence: float = Field(default=1.0, ge=0, le=1)


class ChildChunkInput(StrictModel):
    """A retrieval-sized child chunk. Its access policy is inherited from its parent."""

    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=100_000)
    token_count: int | None = Field(default=None, ge=0)
    chunk_type: str | None = Field(default=None, min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section_path: str | None = Field(default=None, max_length=2000)
    source_locator: dict[str, Any] = Field(default_factory=dict)
    review_status: str | None = Field(default=None, min_length=1, max_length=50)
    source_confidence: float | None = Field(default=None, ge=0, le=1)


class ChunkInput(StrictModel):
    """A context-sized parent chunk with one or more retrieval child chunks.

    Parents and children are both vectorized and stored in Qdrant. Normal retrieval
    deliberately filters to children, then returns the authoritative parent context.
    """

    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=100_000)
    token_count: int | None = Field(default=None, ge=0)
    chunk_type: str = Field(default="text", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section_path: str | None = Field(default=None, max_length=2000)
    source_locator: dict[str, Any] = Field(default_factory=dict)
    access_scope: dict[str, Any] = Field(default_factory=dict)
    review_status: str = Field(default="pending", min_length=1, max_length=50)
    source_confidence: float = Field(default=1.0, ge=0, le=1)
    children: list[ChildChunkInput] = Field(default_factory=list, max_length=500)

    @field_validator("children")
    @classmethod
    def unique_child_ordinals(cls, children: list[ChildChunkInput]) -> list[ChildChunkInput]:
        ordinals = [child.ordinal for child in children]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("chunks[].children[].ordinal must be unique within its parent")
        return children

    @model_validator(mode="after")
    def valid_page_range(self) -> "ChunkInput":
        if self.page_start and self.page_end and self.page_start > self.page_end:
            raise ValueError("page_start must not be greater than page_end")
        return self


class IngestRequest(StrictModel):
    project: ProjectInput
    document: DocumentInput
    parser: ParserInput
    chunks: list[ChunkInput] = Field(min_length=1, max_length=2000)

    @field_validator("chunks")
    @classmethod
    def unique_ordinals(cls, chunks: list[ChunkInput]) -> list[ChunkInput]:
        ordinals = [chunk.ordinal for chunk in chunks]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("chunks[].ordinal must be unique per document version")
        return chunks


class PrincipalInput(StrictModel):
    user_id: str = Field(min_length=1, max_length=200)
    project_ids: list[UUID] = Field(min_length=1, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=100)


class SearchFilters(StrictModel):
    """Safe evidence filters; authorization always remains token-derived."""

    file_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    chunk_types: list[str] | None = Field(default=None, min_length=1, max_length=30)
    content_origins: list[str] | None = Field(default=None, min_length=1, max_length=20)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_page_range(self) -> "SearchFilters":
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must be less than or equal to page_end")
        return self


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=16_000)
    project_id: UUID | None = None
    # Deprecated compatibility field. The authenticated server-side principal is used
    # for every remote search; caller-supplied IDs and roles are ignored.
    principal: PrincipalInput | None = None
    evidence_top_k: int | None = Field(default=None, ge=1, le=50)
    filters: SearchFilters | None = None
    # Images are returned as safe metadata/URLs by default. Full image bytes are always
    # fetched through the protected asset endpoint, never embedded in search results.
    include_images: bool = True
    image_mode: Literal["metadata", "thumbnail", "full"] = "metadata"


class ExternalExportApproval(StrictModel):
    """The true value records an auditable caller acknowledgement before external export."""

    explicit_external_export_approved: Literal[True]

class IngestionJobBatchSubmitRequest(ExternalExportApproval):
    """Submit a bounded group of archived documents after explicit external-export approval."""

    ingestion_job_ids: list[UUID] = Field(min_length=1, max_length=50)

    @field_validator("ingestion_job_ids")
    @classmethod
    def unique_ingestion_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("ingestion_job_ids must be unique")
        return values


class IngestionJobStatusBatchRequest(StrictModel):
    """Fetch only the import records currently being watched by the Admin UI."""

    job_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("job_ids")
    @classmethod
    def unique_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("job_ids must be unique")
        return values


class IngestionJobBatchDeleteRequest(StrictModel):
    """Destructive local-admin action for unmaterialized import records only."""

    job_ids: list[UUID] = Field(min_length=1, max_length=100)
    confirm_delete: Literal[True]

    @field_validator("job_ids")
    @classmethod
    def unique_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("job_ids must be unique")
        return values


class AnswerRequest(ExternalExportApproval):
    """Generates one evidence-constrained answer after explicit external-export approval."""

    query: str = Field(min_length=1, max_length=16_000)
    project_id: UUID
    principal: PrincipalInput
    evidence_top_k: int | None = Field(default=None, ge=1, le=20)

class VisualAnalysisJobCreateRequest(StrictModel):
    """Creates a local visual-analysis job only; it does not export an image."""

    ingestion_job_id: UUID


class VisualAnalysisBatchCreateRequest(StrictModel):
    """Creates at most 20 local jobs for one later, explicitly approved test round."""

    ingestion_job_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("ingestion_job_ids")
    @classmethod
    def unique_ingestion_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("ingestion_job_ids must be unique")
        return values


class VisualAnalysisSubmitRequest(ExternalExportApproval):
    pass


class VisualAnalysisBatchSubmitRequest(VisualAnalysisSubmitRequest):
    job_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("job_ids")
    @classmethod
    def unique_visual_analysis_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("job_ids must be unique")
        return values


class VisualAnalysisQueueBatchRequest(ExternalExportApproval):
    """Explicitly authorize a bounded set of archived images for the local queue."""

    ingestion_job_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("ingestion_job_ids")
    @classmethod
    def unique_ingestion_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("ingestion_job_ids must be unique")
        return values


class VisualAnalysisBatchCancelRequest(StrictModel):
    """Cancel queued image jobs, or request cancellation before a running job materializes."""

    job_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("job_ids")
    @classmethod
    def unique_visual_analysis_job_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("job_ids must be unique")
        return values




class AgentConversationCreateRequest(StrictModel):
    project_id: UUID | None = None


class AgentConversationRenameRequest(StrictModel):
    title: str = Field(min_length=1, max_length=100)


class AgentRunCreateRequest(StrictModel):
    query: str = Field(min_length=1, max_length=8000)
    project_id: UUID | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=100)

class ImportBatchCreateRequest(StrictModel):
    project_external_key: str = Field(min_length=1, max_length=200)
    project_name: str = Field(min_length=1, max_length=500)
    force_reprocess: bool = False


class ImportBatchApproval(StrictModel):
    manifest_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    approve_mineru: bool = False
    approve_vision: bool = False
    schedule_mode: Literal["immediate", "nightly", "custom"] = "immediate"
    scheduled_for: datetime | None = None

class ImportBatchPageReviewUpdate(StrictModel):
    selected_processor: Literal["mineru", "gpt_vision", "mineru_then_gpt"]


class ImportBatchPageReviewBulkUpdate(StrictModel):
    page_review_ids: list[UUID] = Field(min_length=1, max_length=2_000)
    selected_processor: Literal["mineru", "gpt_vision", "mineru_then_gpt"]


class RagEvaluationSuiteCreateRequest(StrictModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)


class RagEvaluationSuiteUpdateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)


class RagEvaluationCaseCreateRequest(StrictModel):
    question: str = Field(min_length=1, max_length=8_000)
    reference_answer: str = Field(min_length=1, max_length=16_000)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=100_000)


class RagEvaluationCaseUpdateRequest(RagEvaluationCaseCreateRequest):
    pass


class RagEvaluationRunCreateRequest(StrictModel):
    """Explicit consent before RAGAS submits selected snippets to the configured model."""

    explicit_external_evaluation_approved: Literal[True]
    evidence_top_k: int = Field(default=6, ge=1, le=20)



class MaterialConsistencySourceSelection(StrictModel):
    source_type: Literal["ingestion_job", "image_asset"]
    source_id: UUID


class MaterialConsistencyAuditCreateRequest(StrictModel):
    project_id: UUID
    sources: list[MaterialConsistencySourceSelection] = Field(min_length=1, max_length=200)
    material_document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    analyze_missing_images: bool = False
    explicit_external_export_approved: Literal[True]

    @field_validator("material_document_ids")
    @classmethod
    def unique_material_document_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("material_document_ids must be unique")
        return values


class MaterialConsistencyAuditCancelRequest(StrictModel):
    confirm_cancel: bool = True
