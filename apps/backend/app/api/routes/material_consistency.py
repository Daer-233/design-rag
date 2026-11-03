"""Local Admin API for effect-render/material consistency audits."""
from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_material_consistency_service
from app.api.schemas import MaterialConsistencyAuditCreateRequest
from app.services.material_consistency_service import MaterialConsistencyError, MaterialConsistencyService

router = APIRouter(prefix="/api/v1/admin/material-consistency", tags=["material-consistency"])
TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@router.get("/sources")
def get_sources(project_id: UUID, service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]) -> dict[str, object]:
    try:
        return service.get_sources(str(project_id))
    except MaterialConsistencyError as error:
        if str(error) == "Project not found":
            raise HTTPException(status_code=404, detail=str(error)) from error
        raise _unprocessable(error) from error


@router.post("/audits", status_code=status.HTTP_201_CREATED)
def create_audit(payload: MaterialConsistencyAuditCreateRequest,
                 service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]) -> dict[str, object]:
    try:
        return service.create_audit(
            project_id=str(payload.project_id),
            source_specs=[{"source_type": item.source_type, "source_id": str(item.source_id)} for item in payload.sources],
            material_document_ids=[str(item) for item in payload.material_document_ids],
            analyze_missing_images=payload.analyze_missing_images,
            explicit_external_export_approved=payload.explicit_external_export_approved,
        )
    except MaterialConsistencyError as error:
        raise _unprocessable(error) from error


@router.get("/audits")
def list_audits(service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)],
                project_id: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
    return {"items": service.repository.list_audits(project_id=str(project_id) if project_id else None, limit=limit)}


@router.get("/audits/{audit_id}")
def get_audit(audit_id: UUID, service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]) -> dict[str, object]:
    audit = service.repository.get_audit(str(audit_id))
    if not audit:
        raise HTTPException(status_code=404, detail="Material consistency audit not found")
    return audit


@router.post("/audits/{audit_id}/cancel")
def cancel_audit(audit_id: UUID, service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]) -> dict[str, object]:
    audit = service.repository.request_cancel(str(audit_id))
    if not audit:
        raise HTTPException(status_code=404, detail="Material consistency audit not found")
    return audit


@router.get("/audits/{audit_id}/events")
async def audit_events(audit_id: UUID, service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]):
    """Replayable SSE state stream; the page never needs a global refresh timer."""
    async def stream():
        previous = None
        while True:
            audit = await asyncio.to_thread(service.repository.get_audit, str(audit_id))
            if not audit:
                yield "event: error\ndata: {\"detail\": \"Material consistency audit not found\"}\n\n"
                return
            event_payload = {
                "id": str(audit["id"]), "status": audit["status"], "progress": audit.get("progress") or {},
                "summary": audit.get("summary") or {}, "updated_at": audit.get("updated_at"),
                "error_code": audit.get("error_code"), "error_message": audit.get("error_message"),
            }
            encoded = json.dumps(event_payload, ensure_ascii=False, default=str)
            if encoded != previous:
                event = "completed" if audit["status"] in TERMINAL else "progress"
                yield f"event: {event}\ndata: {encoded}\n\n"
                previous = encoded
            if audit["status"] in TERMINAL:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/sources/{source_type}/{source_id}/preview")
def preview_source(source_type: str, source_id: UUID, project_id: UUID,
                   service: Annotated[MaterialConsistencyService, Depends(get_material_consistency_service)]):
    """Serve a local EXIF-free derivative only; internal archive paths never leave the server."""
    source = service.repository.get_source(project_id=str(project_id), source_type=source_type, source_id=str(source_id), include_local_path=True)
    if not source:
        raise HTTPException(status_code=404, detail="Preview source not found")
    try:
        from pathlib import Path
        local_path = Path(str(source.get("local_path") or ""))
        if not local_path.is_file():
            raise MaterialConsistencyError("Local preview derivative is unavailable")
        derivative = service.image_preprocessor.prepare(local_path, source_sha256=service._source_hash(source, local_path))
    except (MaterialConsistencyError, OSError, ValueError) as error:
        raise _unprocessable(error) from error
    return FileResponse(derivative.derivative_path, media_type=derivative.content_type, filename="material-consistency-preview.jpg")
