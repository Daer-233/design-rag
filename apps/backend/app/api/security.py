"""Bearer-token authentication for the agent evidence-only search API.

Tokens and project scopes are configured server-side.  Remote callers cannot supply
or enlarge their own roles or project membership.
"""

from __future__ import annotations

import json
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.services.access_policy import Principal

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _agent_principals() -> list[tuple[str, Principal]]:
    """Return server-configured agent tokens without exposing token values.

    DESIGN_RAG_AGENT_TOKENS is a JSON object keyed by the bearer token, for example:
    {"token-a": {"agent_id": "agent-a", "project_ids": ["<uuid>"], "roles": ["designer"]}}
    A legacy DESIGN_RAG_API_TOKEN remains supported for existing local callers.
    """
    principals: list[tuple[str, Principal]] = []
    raw = settings.design_rag_agent_tokens
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            entries = {}
        if isinstance(entries, dict):
            for token, config in entries.items():
                if not isinstance(token, str) or not token or not isinstance(config, dict):
                    continue
                project_ids = config.get("project_ids", [])
                roles = config.get("roles", [])
                if not isinstance(project_ids, list) or not isinstance(roles, list):
                    continue
                principals.append(
                    (
                        token,
                        Principal(
                            user_id=str(config.get("agent_id") or config.get("display_name") or "readonly-agent"),
                            project_ids={str(item) for item in project_ids},
                            roles={str(item) for item in roles},
                            all_projects=bool(config.get("all_projects", False)),
                        ),
                    )
                )
    if settings.design_rag_api_token:
        principals.append(
            (
                settings.design_rag_api_token,
                Principal(
                    user_id=settings.design_rag_api_principal_user_id,
                    project_ids={str(item) for item in settings.design_rag_api_allowed_project_ids},
                    roles=set(settings.design_rag_api_principal_roles),
                    all_projects=not bool(settings.design_rag_api_allowed_project_ids),
                ),
            )
        )
    return principals


def get_readonly_search_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> Principal:
    """Authenticate a server-configured read-only agent principal."""
    principals = _agent_principals()
    if not principals:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent search API is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    matched: Principal | None = None
    # Check every configured token before deciding, avoiding early-exit comparison behaviour.
    for token, principal in principals:
        if compare_digest(credentials.credentials, token):
            matched = principal
    if matched is None:
        raise _unauthorized()
    return matched
