"""Local Admin batch-import endpoints: archive, preview, explicitly approve, then queue."""

from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
import asyncio
import json
from pathlib import Path

from app.api.dependencies import get_import_batch_service
from app.api.schemas import (
    ImportBatchApproval, ImportBatchCreateRequest, ImportBatchPageReviewBulkUpdate, ImportBatchPageReviewUpdate,
)
from app.services.import_batch_service import ImportBatchError

router = APIRouter(prefix="/api/v1/admin/import-batches", tags=["administration"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_import_batch(payload: ImportBatchCreateRequest) -> dict[str, object]:
    try:
        return get_import_batch_service().create(
            project_external_key=payload.project_external_key, project_name=payload.project_name,
            force_reprocess=payload.force_reprocess,
        )
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_import_batch_file(batch_id: UUID, file: UploadFile = File(...),
                                   source_relative_path: str | None = Form(default=None)) -> dict[str, object]:
    try:
        return await get_import_batch_service().upload_file(
            batch_id=str(batch_id), file=file, source_relative_path=source_relative_path
        )
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/prepare")
def prepare_import_batch(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().prepare(str(batch_id))
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/retry-page-processing")
def retry_import_batch_page_processing(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().retry_page_processing(str(batch_id))
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

@router.get("/{batch_id}/page-reviews")
def list_page_reviews(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().list_page_reviews(str(batch_id))
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{batch_id}/page-reviews/{page_review_id}/preview")
def preview_page_review(batch_id: UUID, page_review_id: UUID):
    try:
        review = get_import_batch_service().get_page_review(str(batch_id), str(page_review_id), include_local_path=True)
        local_path = Path(str(review.get("preview_file_path") or ""))
        if not local_path.is_file():
            raise ImportBatchError("Local page preview is unavailable")
        return FileResponse(local_path, media_type=str(review.get("preview_content_type") or "image/jpeg"),
                            filename=f"page-{int(review['source_page_number']):04d}.jpg",
                            headers={"Cache-Control": "private, max-age=3600", "ETag": str(review.get("preview_sha256") or "")})
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND if "not found" in str(error).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.patch("/{batch_id}/page-reviews/{page_review_id}")
def update_page_review(batch_id: UUID, page_review_id: UUID, payload: ImportBatchPageReviewUpdate) -> dict[str, object]:
    try:
        return get_import_batch_service().update_page_review(str(batch_id), str(page_review_id), payload.selected_processor)
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/page-reviews/bulk-update")
def bulk_update_page_reviews(batch_id: UUID, payload: ImportBatchPageReviewBulkUpdate) -> dict[str, object]:
    try:
        return get_import_batch_service().bulk_update_page_reviews(
            str(batch_id), [str(page_id) for page_id in payload.page_review_ids], payload.selected_processor
        )
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/page-reviews/confirm")
def confirm_page_reviews(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().confirm_page_reviews(str(batch_id))
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/{batch_id}/page-reviews/reopen")
def reopen_page_reviews(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().reopen_page_reviews(str(batch_id))
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{batch_id}/events")
async def import_batch_events(batch_id: UUID):
    service = get_import_batch_service()
    async def stream():
        last_payload = ""
        while True:
            try:
                payload = service.get(str(batch_id))
                encoded = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
                if encoded != last_payload:
                    yield f"event: batch\ndata: {encoded}\n\n"
                    last_payload = encoded
                if payload.get("status") in {"succeeded", "partially_succeeded", "failed"}:
                    yield "event: complete\ndata: {}\n\n"
                    return
            except ImportBatchError as error:
                yield f"event: error\ndata: {json.dumps({'detail': str(error)}, ensure_ascii=False)}\n\n"
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("")
def list_import_batches(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                        project_id: UUID | None = None) -> dict[str, object]:
    return get_import_batch_service().list(limit=limit, offset=offset, project_id=str(project_id) if project_id else None)


@router.get("/{batch_id}")
def get_import_batch(batch_id: UUID) -> dict[str, object]:
    try:
        return get_import_batch_service().get(str(batch_id))
    except ImportBatchError as error:
        code = status.HTTP_404_NOT_FOUND if str(error) == "Import batch not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.post("/{batch_id}/approve")
def approve_import_batch(batch_id: UUID, payload: ImportBatchApproval) -> dict[str, object]:
    try:
        return get_import_batch_service().approve(
            batch_id=str(batch_id), manifest_sha256=payload.manifest_sha256,
            approve_mineru=payload.approve_mineru, approve_vision=payload.approve_vision,
            schedule_mode=payload.schedule_mode, scheduled_for=payload.scheduled_for,
        )
    except ImportBatchError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

