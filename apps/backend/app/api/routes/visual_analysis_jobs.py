"""Durable, explicitly authorized queued GPT visual-analysis endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import get_visual_analysis_service
from app.api.schemas import (
    VisualAnalysisBatchCancelRequest,
    VisualAnalysisBatchCreateRequest,
    VisualAnalysisBatchSubmitRequest,
    VisualAnalysisJobCreateRequest,
    VisualAnalysisQueueBatchRequest,
    VisualAnalysisSubmitRequest,
)
from app.services.visual_analysis_service import VisualAnalysisJobError

router = APIRouter(prefix="/api/v1/visual-analysis-jobs", tags=["visual-analysis"])


@router.get("/queue-info")
def get_visual_analysis_queue_info() -> dict[str, object]:
    """Return safe local UI metadata; no secret/provider configuration is exposed."""
    return get_visual_analysis_service().queue_info()


@router.post("/queue-batch", status_code=status.HTTP_202_ACCEPTED)
def queue_visual_analysis_job_batch(payload: VisualAnalysisQueueBatchRequest) -> dict[str, object]:
    """Queue approved images locally and return before any external analysis starts."""
    assert payload.explicit_external_export_approved is True
    try:
        results = get_visual_analysis_service().queue_batch([str(item) for item in payload.ingestion_job_ids])
        jobs = [result["job"] for result in results if result.get("job")]
        return {"count": len(results), "jobs": jobs, "results": results}
    except VisualAnalysisJobError as error:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "not configured" in str(error) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/cancel-batch")
def cancel_visual_analysis_job_batch(payload: VisualAnalysisBatchCancelRequest) -> dict[str, object]:
    """Cancel queued jobs or request cancellation before a running result materializes."""
    try:
        results = get_visual_analysis_service().cancel_batch([str(item) for item in payload.job_ids])
        return {"count": len(results), "results": results}
    except VisualAnalysisJobError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("", status_code=status.HTTP_201_CREATED)
def create_visual_analysis_job(payload: VisualAnalysisJobCreateRequest) -> dict[str, object]:
    """Create a local draft image job; it does not export image bytes."""
    try:
        return get_visual_analysis_service().create(str(payload.ingestion_job_id))
    except VisualAnalysisJobError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/batch", status_code=status.HTTP_201_CREATED)
def create_visual_analysis_job_batch(payload: VisualAnalysisBatchCreateRequest) -> dict[str, object]:
    """Prepare at most 20 local draft jobs. This operation does not export images."""
    try:
        results = get_visual_analysis_service().create_batch([str(item) for item in payload.ingestion_job_ids])
        jobs = [result["job"] for result in results if result.get("outcome") == "created"]
        return {"count": len(results), "jobs": jobs, "results": results}
    except VisualAnalysisJobError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/submit-batch", status_code=status.HTTP_202_ACCEPTED)
def submit_visual_analysis_job_batch(payload: VisualAnalysisBatchSubmitRequest) -> dict[str, object]:
    """Backward-compatible batch operation: enqueue local jobs without inline analysis."""
    assert payload.explicit_external_export_approved is True
    try:
        results = get_visual_analysis_service().submit_batch([str(item) for item in payload.job_ids])
        jobs = [result["job"] for result in results if result.get("job")]
        return {"count": len(results), "jobs": jobs, "results": results}
    except VisualAnalysisJobError as error:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "not configured" in str(error) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.get("/{job_id}")
def get_visual_analysis_job(job_id: str) -> dict[str, object]:
    try:
        return get_visual_analysis_service().get(job_id)
    except VisualAnalysisJobError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/{job_id}/submit", status_code=status.HTTP_202_ACCEPTED)
def submit_visual_analysis_job(job_id: str, payload: VisualAnalysisSubmitRequest) -> dict[str, object]:
    """Backward-compatible operation: queue an already-created job without blocking."""
    assert payload.explicit_external_export_approved is True
    try:
        return get_visual_analysis_service().submit(job_id)
    except VisualAnalysisJobError as error:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "not configured" in str(error) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/{job_id}/cancel")
def cancel_visual_analysis_job(job_id: str) -> dict[str, object]:
    try:
        return get_visual_analysis_service().cancel(job_id)
    except VisualAnalysisJobError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
