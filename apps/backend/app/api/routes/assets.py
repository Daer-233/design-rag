"""Safe image asset delivery for Chat and the authenticated MCP gateway."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_agent_repository, get_repository
from app.api.security import get_readonly_search_principal
from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.rag_repository import RagRepository
from app.services.access_policy import AccessDeniedError, AccessPolicy
from app.services.image_asset_access import verify_asset_access, sign_asset_access

router = APIRouter(prefix="/api/v1", tags=["image-assets"])
_policy = AccessPolicy()


def _safe_uuid(value: str, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid {label}") from exc


def _candidate_paths(row: dict[str, object], variant: str) -> list[Path]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    keys: list[object] = []
    if variant == "thumbnail":
        keys.extend([metadata.get("thumbnail_path"), metadata.get("thumbnail_storage_key")])
    if variant == "preview":
        keys.extend([metadata.get("preview_path"), metadata.get("preview_storage_key")])
    keys.extend([
        row.get("derivative_storage_key") if variant != "original" else None,
        row.get("source_storage_key"),
        row.get("storage_path"),
    ])
    result: list[Path] = []
    root = settings.data_root.resolve()
    for raw in keys:
        if not raw:
            continue
        raw_path = str(raw)
        if raw_path.startswith("local://"):
            raw_path = raw_path[len("local://"):].lstrip("/\\")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved not in result:
            result.append(resolved)
    return result


def _serve_asset(row: dict[str, object], variant: str) -> FileResponse:
    paths = _candidate_paths(row, variant)
    if not paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image asset file is unavailable")
    content_type = str(row.get("content_type") or "image/jpeg").lower()
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return FileResponse(
        paths[0],
        media_type=content_type,
        filename=Path(str(row.get("asset_file_name") or row.get("file_name") or paths[0].name)).name,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


def _chat_client_id(request: Request) -> str | None:
    value = request.cookies.get(settings.agent_cookie_name)
    try:
        return str(UUID(value)) if value else None
    except (ValueError, TypeError):
        return None


@router.get("/chat/assets/{asset_id}/{variant}")
def chat_asset(
    asset_id: str,
    variant: str,
    request: Request,
    repository: Annotated[RagRepository, Depends(get_repository)],
    agent_repository: Annotated[AgentRepository, Depends(get_agent_repository)],
    access_token: str = Query(..., min_length=20),
    run_id: str = Query(...),
):
    if variant not in {"thumbnail", "preview", "original"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown image variant")
    asset_id = _safe_uuid(asset_id, "asset identifier")
    run_id = _safe_uuid(run_id, "run identifier")
    client_id = _chat_client_id(request)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chat session is required")
    row = repository.get_image_asset(asset_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image asset not found")
    project_id = str(row.get("project_id"))
    if verify_asset_access(access_token, asset_id, project_id, run_id=run_id, client_id=client_id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired image reference")
    if not agent_repository.run_allows_asset(client_id, run_id, asset_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Image is not part of this Chat run")
    return _serve_asset(row, variant)


@router.get("/mcp/assets/{asset_id}/{variant}")
def mcp_asset(
    asset_id: str,
    variant: str,
    repository: Annotated[RagRepository, Depends(get_repository)],
    project_id: UUID = Query(...),
    principal=Depends(get_readonly_search_principal),
):
    if variant not in {"thumbnail", "preview", "original"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown image variant")
    asset_id = _safe_uuid(asset_id, "asset identifier")
    project_id_text = str(project_id)
    try:
        _policy.require_project_access(principal, project_id_text)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    row = repository.get_image_asset(asset_id, project_id=project_id_text)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image asset not found")
    return _serve_asset(row, variant)


@router.get("/mcp/chunks/{chunk_id}/images")
def mcp_chunk_images(
    chunk_id: str,
    repository: Annotated[RagRepository, Depends(get_repository)],
    project_id: UUID = Query(...),
    principal=Depends(get_readonly_search_principal),
):
    chunk_id = _safe_uuid(chunk_id, "chunk identifier")
    project_id_text = str(project_id)
    try:
        _policy.require_project_access(principal, project_id_text)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    rows = repository.list_retrieval_image_assets([chunk_id], project_id=project_id_text, limit=100)
    return {"project_id": project_id_text, "chunk_id": chunk_id, "images": [_public_mcp_asset(row) for row in rows]}


@router.get("/mcp/documents/{document_id}/pages/{page_number}")
def mcp_document_page(
    document_id: str,
    page_number: int,
    repository: Annotated[RagRepository, Depends(get_repository)],
    project_id: UUID = Query(...),
    principal=Depends(get_readonly_search_principal),
):
    document_id = _safe_uuid(document_id, "document identifier")
    if page_number < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="page_number must be positive")
    project_id_text = str(project_id)
    try:
        _policy.require_project_access(principal, project_id_text)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    rows = repository.list_document_page_image_assets(document_id, page_number, project_id=project_id_text)
    return {
        "project_id": project_id_text,
        "document_id": document_id,
        "page_number": page_number,
        "images": [_public_mcp_asset(row) for row in rows],
    }


def _public_mcp_asset(row: dict[str, object]) -> dict[str, object]:
    asset_id = str(row["id"])
    project_id = str(row["project_id"])
    token = sign_asset_access(asset_id, project_id)
    query = f"?project_id={project_id}&access_token={token}"
    prefix = f"/api/v1/mcp/assets/{asset_id}"
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "asset_id": asset_id,
        "project_id": project_id,
        "document_id": str(row.get("document_id")) if row.get("document_id") else None,
        "document_name": row.get("file_name") or row.get("asset_file_name"),
        "page_number": row.get("page_number"),
        "asset_type": metadata.get("asset_type") or row.get("image_classification") or "unknown",
        "thumbnail_url": f"{prefix}/thumbnail{query}",
        "preview_url": f"{prefix}/preview{query}",
        "original_url": f"{prefix}/original{query}",
        "resource_uri": f"design-rag://assets/{asset_id}",
    }
