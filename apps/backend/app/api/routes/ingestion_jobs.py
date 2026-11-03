"""API endpoints for durable MinerU ingestion jobs."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.dependencies import get_ingestion_job_service
from app.api.schemas import ExternalExportApproval, IngestionJobBatchSubmitRequest
from app.services.ingestion_job_service import IngestionJobError

router = APIRouter(prefix="/api/v1/ingestion-jobs", tags=["ingestion"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ingestion_job(
    file: UploadFile = File(...),
    project_external_key: str = Form(...),
    project_name: str = Form(...),
    document_external_key: str | None = Form(default=None),
    access_scope_json: str | None = Form(default=None),
    document_metadata_json: str | None = Form(default=None),
    processing_profile: str | None = Form(default=None),
) -> dict[str, object]:
    try:
        return await get_ingestion_job_service().create_from_upload(
            file=file,
            project_external_key=project_external_key,
            project_name=project_name,
            document_external_key=document_external_key,
            access_scope_json=access_scope_json,
            document_metadata_json=document_metadata_json,
            processing_profile=processing_profile,
        )
    except IngestionJobError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{job_id}")
def get_ingestion_job(job_id: str) -> dict[str, object]:
    try:
        return get_ingestion_job_service().get(job_id)
    except IngestionJobError as error:
        code = status.HTTP_404_NOT_FOUND if str(error) == "Ingestion job not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/{job_id}/reimport", status_code=status.HTTP_201_CREATED)
def reimport_ingestion_job(job_id: str) -> dict[str, object]:
    """Prepare a new local retry from the archived source without external export."""
    try:
        return get_ingestion_job_service().reimport(job_id)
    except IngestionJobError as error:
        code = status.HTTP_404_NOT_FOUND if "not found" in str(error).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error
@router.post("/submit-batch", status_code=status.HTTP_202_ACCEPTED)
def submit_ingestion_job_batch(payload: IngestionJobBatchSubmitRequest) -> dict[str, object]:
    """Explicitly submit selected non-image documents to MinerU, one result per record."""
    assert payload.explicit_external_export_approved is True
    results = get_ingestion_job_service().submit_batch([str(item) for item in payload.ingestion_job_ids])
    return {"count": len(results), "results": results}


@router.post("/{job_id}/submit", status_code=status.HTTP_202_ACCEPTED)
def submit_ingestion_job(job_id: str, payload: ExternalExportApproval) -> dict[str, object]:
    """Explicitly exports the archived source file to MinerU after the caller's policy check."""
    assert payload.explicit_external_export_approved is True
    try:
        return get_ingestion_job_service().enqueue(job_id)
    except IngestionJobError as error:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "not configured" in str(error) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/{job_id}/poll")
def poll_ingestion_job(job_id: str) -> dict[str, object]:
    try:
        return get_ingestion_job_service().poll(job_id)
    except IngestionJobError as error:
        code = status.HTTP_404_NOT_FOUND if str(error) == "Ingestion job not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error
@router.post("/{job_id}/materialize")
def materialize_ingestion_job(job_id: str) -> dict[str, object]:
    """Normalize an archived MinerU result into authoritative parent-child chunks."""
    try:
        return get_ingestion_job_service().materialize(job_id)
    except IngestionJobError as error:
        code = status.HTTP_404_NOT_FOUND if str(error) == "Ingestion job not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error

