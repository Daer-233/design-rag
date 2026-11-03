"""Local administration endpoints for retrieval-quality optimization."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_ingestion_job_service, get_repository, get_search_audit_repository
from app.api.schemas import IngestionJobBatchDeleteRequest, IngestionJobStatusBatchRequest
from app.repositories.rag_repository import RagRepository
from app.repositories.search_audit_repository import SearchAuditRepository

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])


@router.get("/projects")
def list_projects(
    repository: Annotated[SearchAuditRepository, Depends(get_search_audit_repository)],
) -> dict[str, object]:
    """List projects for the local administration dashboard's filter menu."""
    return {"items": repository.list_projects()}



def _import_record(job: dict[str, object]) -> dict[str, object]:
    """Expose one Admin import record with its *effective* knowledge-base state.

    Image imports deliberately retain their original local ingestion job in ``created``
    after GPT Vision takes over.  The derived document version is the real completion
    signal, so the Admin UI must never present that case as "待继续".
    """
    fields = (
        "id", "document_id", "document_version_id", "provider_name", "status",
        "source_file_name", "source_content_type", "source_size_bytes",
        "processing_route", "route_reason", "provider_task_id", "provider_batch_id",
        "retry_count", "error_code", "error_message", "trace_id",
        "created_at", "submitted_at", "completed_at", "updated_at", "project_id",
        "document_external_key", "file_name", "file_type", "source_document_name",
        "project_external_key", "project_name", "visual_analysis_job_id",
        "visual_analysis_status", "visual_analysis_error_code",
        "visual_analysis_error_message", "visual_analysis_created_at",
        "visual_analysis_updated_at", "visual_analysis_completed_at",
        "derived_document_version_id", "record_disposition", "canonical_job_id",
        "duplicate_attempt_count", "successful_attempt_count",
        "active_superseded_attempt_count", "superseded_attempt_count",
    )
    record = {field: job.get(field) for field in fields if field in job}
    materialized = bool(record.get("document_version_id") or record.get("derived_document_version_id"))
    visual_status = record.get("visual_analysis_status")
    status = str(record.get("status") or "created")
    if materialized:
        final_status, final_status_label = "materialized", "已写入知识库"
    elif visual_status == "failed" or status == "failed":
        final_status, final_status_label = "failed", "处理失败"
    elif visual_status in {"queued", "running"} or status in {"queued", "processing", "submitted"}:
        final_status, final_status_label = "processing", "处理中"
    elif status == "cancelled" or visual_status == "cancelled":
        final_status, final_status_label = "cancelled", "已取消"
    else:
        final_status, final_status_label = "pending", "待提交"
    materialized_at = record.get("completed_at") if record.get("document_version_id") else record.get("visual_analysis_completed_at")
    record.update({
        "is_materialized": materialized,
        "final_status": final_status,
        "final_status_label": final_status_label,
        "materialized_at": materialized_at,
        "archived_at": materialized_at or record.get("completed_at"),
    })
    return record


@router.get("/ingestion-jobs")
def list_ingestion_jobs(
    repository: Annotated[RagRepository, Depends(get_repository)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    project_id: UUID | None = None,
    status: Literal["created", "queued", "processing", "submitted", "succeeded", "failed", "cancelled"] | None = None,
    include_superseded: bool = Query(default=False),
) -> dict[str, object]:
    """List local import lifecycle records for the Admin dashboard."""
    records = repository.list_ingestion_jobs(
        limit=limit,
        offset=offset,
        project_id=str(project_id) if project_id else None,
        status=status,
        include_superseded=include_superseded,
    )
    return {"items": [_import_record(job) for job in records["items"]], "total": records["total"]}


@router.delete("/ingestion-jobs")
def delete_ingestion_jobs(payload: IngestionJobBatchDeleteRequest) -> dict[str, object]:
    """Delete safe unmaterialized records from the local dashboard only.

    This endpoint is not included by ``app.remote_main``.
    """
    assert payload.confirm_delete is True
    results = get_ingestion_job_service().delete_batch([str(item) for item in payload.job_ids])
    return {"count": len(results), "results": results}


@router.post("/ingestion-jobs/status-batch")
def get_ingestion_job_status_batch(
    payload: IngestionJobStatusBatchRequest,
    repository: Annotated[RagRepository, Depends(get_repository)],
) -> dict[str, object]:
    """Refresh only visible active rows without reloading the complete list."""
    records = repository.list_ingestion_jobs_by_ids([str(job_id) for job_id in payload.job_ids])
    return {"items": [_import_record(job) for job in records]}


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job_record(
    job_id: UUID,
    repository: Annotated[RagRepository, Depends(get_repository)],
) -> dict[str, object]:
    """Return one import record and its local visual-analysis attempts."""
    job = repository.get_ingestion_job(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    visual_jobs = repository.list_visual_analysis_jobs(str(job_id))
    latest_visual = visual_jobs[0] if visual_jobs else {}
    enriched_job = {
        **job,
        "visual_analysis_status": latest_visual.get("status"),
        "derived_document_version_id": latest_visual.get("derived_document_version_id"),
        "visual_analysis_completed_at": latest_visual.get("completed_at"),
    }
    return {"job": _import_record(enriched_job), "visual_jobs": visual_jobs}
@router.get("/search-audit")
def list_search_audit(
    repository: Annotated[SearchAuditRepository, Depends(get_search_audit_repository)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    project_id: UUID | None = None,
    actor_id: str | None = Query(default=None, min_length=1, max_length=200),
    outcome: Literal["succeeded", "denied", "failed"] | None = None,
    query: str | None = Query(default=None, min_length=1, max_length=16_000),
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, object]:
    """Return only operational audit data; source bytes and source locators remain private."""
    return repository.list_records(
        limit=limit,
        offset=offset,
        project_id=str(project_id) if project_id else None,
        actor_id=actor_id,
        outcome=outcome,
        query=query,
        since=since,
        until=until,
    )


@router.get("/search-audit/summary")
def search_audit_summary(
    repository: Annotated[SearchAuditRepository, Depends(get_search_audit_repository)],
    days: int = Query(default=30, ge=1, le=3650),
) -> dict[str, object]:
    """Provide aggregate signals for detecting unanswered or slow searches."""
    return repository.summary(days=days)
