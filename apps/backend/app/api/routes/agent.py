"""Temporary anonymous local Agent HTTP API with replayable SSE workflow events."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_agent_service
from app.api.schemas import (
    AgentConversationCreateRequest,
    AgentConversationRenameRequest,
    AgentRunCreateRequest,
)
from app.core.config import settings
from app.services.agent_service import AgentService

router = APIRouter(prefix="/api/v1/agent", tags=["anonymous-agent"])


def _anonymous_client_id(request: Request, response: Response, service: AgentService) -> str:
    candidate = request.cookies.get(settings.agent_cookie_name)
    try:
        if candidate:
            client_id = str(UUID(candidate))
        else:
            raise ValueError
    except (ValueError, TypeError):
        client_id = service.new_anonymous_client_id()
        response.set_cookie(
            key=settings.agent_cookie_name,
            value=client_id,
            httponly=True,
            samesite="lax",
            secure=False,  # Local HTTP development only. HTTPS cookie settings are a later deployment task.
            max_age=60 * 60 * 24 * 365,
            path="/",
        )
    return service.ensure_client(client_id)


def _require_enabled() -> None:
    if not settings.agent_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent is disabled")


@router.get("/session")
def get_session(
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    return {
        "mode": "anonymous_local",
        "client_id_hint": client_id[-8:],
        "note": "当前为临时匿名模式：同一浏览器保留自己的会话，未使用 IP 作为身份。",
    }


@router.get("/projects")
def list_projects(
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    """List projects through the same MCP-backed catalog used by the Chat workflow."""
    _require_enabled()
    return {"items": service.list_projects()}

@router.get("/conversations")
def list_conversations(
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    return {"items": service.list_conversations(client_id)}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: AgentConversationCreateRequest,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    conversation = service.create_conversation(
        client_id,
        project_id=str(payload.project_id) if payload.project_id is not None else None,
    )
    return {"conversation": conversation}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    conversation = service.get_conversation(client_id, str(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return {"conversation": conversation, "messages": service.list_messages(client_id, str(conversation_id))}


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    payload: AgentConversationRenameRequest,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    conversation = service.rename_conversation(client_id, str(conversation_id), payload.title)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return {"conversation": conversation}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: UUID,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> Response:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    if not service.delete_conversation(client_id, str(conversation_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    conversation_id: UUID,
    payload: AgentRunCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    try:
        created = service.start_run(
            anonymous_client_id=client_id,
            conversation_id=str(conversation_id),
            query=payload.query,
            project_id=str(payload.project_id) if payload.project_id is not None else None,
            request_id=payload.request_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    run = created["run"]
    if not created["reused"]:
        background_tasks.add_task(service.execute_run, anonymous_client_id=client_id, run_id=str(run["id"]))
    return {"run": run, "reused": created["reused"], "events_url": f"/api/v1/agent/runs/{run['id']}/events"}


@router.get("/runs/{run_id}")
def get_run(
    run_id: UUID,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    run = service.get_run(client_id, str(run_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return {"run": run}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: UUID,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> dict[str, object]:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    if not service.cancel_run(client_id, str(run_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run cannot be cancelled")
    return {"cancelled": True, "run_id": str(run_id)}


@router.get("/runs/{run_id}/events")
def stream_run_events(
    run_id: UUID,
    request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> StreamingResponse:
    _require_enabled()
    client_id = _anonymous_client_id(request, response, service)
    if service.get_run(client_id, str(run_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    raw_last_event_id = request.headers.get("Last-Event-ID", "0")
    try:
        last_event_id = max(0, int(raw_last_event_id))
    except ValueError:
        last_event_id = 0
    return StreamingResponse(
        service.stream_events(client_id, str(run_id), after_id=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
