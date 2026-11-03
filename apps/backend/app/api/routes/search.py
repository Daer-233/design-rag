"""Authenticated evidence retrieval endpoint; generation is intentionally not exposed."""

from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import (
    get_knowledge_catalog_repository,
    get_search_audit_service,
    get_search_service,
)
from app.api.schemas import SearchRequest
from app.api.security import get_readonly_search_principal
from app.clients.inference import InferenceServiceError
from app.repositories.knowledge_catalog_repository import KnowledgeCatalogRepository
from app.repositories.search_audit_repository import SearchAuditWrite
from app.services.access_policy import AccessDeniedError, Principal

router = APIRouter(prefix="/api/v1", tags=["retrieval"])

_REQUESTER_ID_HEADER = "X-Design-RAG-Requester-ID"
_REQUESTER_NAME_HEADER = "X-Design-RAG-Requester-Name"


def _optional_header(request: Request, name: str, *, max_length: int = 200) -> str | None:
    value = request.headers.get(name)
    if value is None:
        return None
    value = value.strip()
    return value[:max_length] if value else None


def _audit_identity(request: Request, principal: Principal) -> tuple[str, str | None, str]:
    """Audit the authenticated token identity, never caller-controlled headers."""
    del request
    return principal.user_id, None, "token_principal"


def _record_search_audit(
    *,
    request: Request,
    payload: SearchRequest,
    principal: Principal,
    started: float,
    outcome: str,
    result: dict[str, object] | None = None,
    error_code: str | None = None,
) -> None:
    actor_id, actor_display_name, identity_source = _audit_identity(request, principal)
    results = result.get("results", []) if isinstance(result, dict) else []
    chunk_ids = [
        str(item["chunk_id"])
        for item in results
        if isinstance(item, dict) and item.get("chunk_id") is not None
    ] if isinstance(results, list) else []
    client = request.client
    get_search_audit_service().record_safely(
        SearchAuditWrite(
            actor_id=actor_id,
            actor_display_name=actor_display_name,
            identity_source=identity_source,
            principal_roles=sorted(principal.roles),
            project_id=str(payload.project_id) if payload.project_id is not None else None,
            query_text=payload.query,
            requested_evidence_top_k=payload.evidence_top_k,
            result_chunk_ids=chunk_ids,
            outcome=outcome,
            error_code=error_code,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            client_ip=client.host if client else None,
            client_user_agent=_optional_header(request, "User-Agent", max_length=1000),
        )
    )


@router.get("/projects")
def list_projects(
    principal: Annotated[Principal, Depends(get_readonly_search_principal)],
    repository: Annotated[KnowledgeCatalogRepository, Depends(get_knowledge_catalog_repository)],
) -> dict[str, object]:
    """List only active projects visible to the authenticated read-only token."""
    return {"projects": repository.list_projects(principal)}


@router.get("/projects/{project_id}/documents")
def list_project_documents(
    project_id: UUID,
    principal: Annotated[Principal, Depends(get_readonly_search_principal)],
    repository: Annotated[KnowledgeCatalogRepository, Depends(get_knowledge_catalog_repository)],
) -> dict[str, object]:
    """List safe document metadata for one authorized project."""
    try:
        documents = repository.list_project_documents(principal, str(project_id))
    except AccessDeniedError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return {"project_id": str(project_id), "documents": documents}


@router.get("/retrieval-capabilities")
def retrieval_capabilities(
    _principal: Annotated[Principal, Depends(get_readonly_search_principal)],
    repository: Annotated[KnowledgeCatalogRepository, Depends(get_knowledge_catalog_repository)],
) -> dict[str, object]:
    """Describe the read-only retrieval contract exposed through the MCP gateway."""
    return repository.capabilities()

@router.post("/search")
def search(
    payload: SearchRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_readonly_search_principal)],
) -> dict[str, object]:
    """Return only evidence and safe citation fields for the authenticated principal."""
    started = perf_counter()
    try:
        result = get_search_service().search(
            query=payload.query,
            project_id=str(payload.project_id) if payload.project_id is not None else None,
            # Never trust payload.principal: roles and project membership come from the token's
            # server-side configuration, preventing a remote caller from escalating access.
            principal_raw=principal,
            evidence_top_k=payload.evidence_top_k,
            retrieval_filters=payload.filters.model_dump(exclude_none=True) if payload.filters else None,
            redact_source_details=True,
            include_images=payload.include_images,
            image_mode=payload.image_mode,
            public_base_url=str(request.base_url).rstrip("/"),
            asset_url_prefix="/api/v1/mcp/assets",
        )
    except AccessDeniedError as error:
        _record_search_audit(
            request=request,
            payload=payload,
            principal=principal,
            started=started,
            outcome="denied",
            error_code="access_denied",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except InferenceServiceError as error:
        _record_search_audit(
            request=request,
            payload=payload,
            principal=principal,
            started=started,
            outcome="failed",
            error_code="inference_service_error",
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except Exception as error:
        _record_search_audit(
            request=request,
            payload=payload,
            principal=principal,
            started=started,
            outcome="failed",
            error_code=type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrieval failed: {type(error).__name__}",
        ) from error
    _record_search_audit(
        request=request,
        payload=payload,
        principal=principal,
        started=started,
        outcome="succeeded",
        result=result,
    )
    return result

