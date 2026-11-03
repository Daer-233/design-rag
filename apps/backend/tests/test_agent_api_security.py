import json
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.api import security
from app.api.routes import answer as answer_route
from app.core.config import settings


def test_agent_token_is_scoped_to_configured_projects(monkeypatch) -> None:
    monkeypatch.setattr(settings, "design_rag_api_token", None)
    monkeypatch.setattr(settings, "design_rag_agent_tokens", json.dumps({"agent-token": {"agent_id": "agent-a", "project_ids": ["project-a"], "roles": ["designer"]}}))
    principal = security.get_readonly_search_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="agent-token"))
    assert principal.user_id == "agent-a"
    assert principal.project_ids == {"project-a"}
    assert principal.all_projects is False
    assert principal.roles == {"designer"}


def test_agent_token_rejects_missing_or_wrong_value(monkeypatch) -> None:
    monkeypatch.setattr(settings, "design_rag_api_token", None)
    monkeypatch.setattr(settings, "design_rag_agent_tokens", '{"agent-token":{"agent_id":"agent-a"}}')
    with pytest.raises(HTTPException) as missing:
        security.get_readonly_search_principal(None)
    with pytest.raises(HTTPException) as wrong:
        security.get_readonly_search_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-token"))
    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401


def test_answer_api_is_disabled_in_agent_retrieval_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "answer_api_enabled", False)
    with pytest.raises(HTTPException) as disabled:
        answer_route.answer(None)  # type: ignore[arg-type]
    assert disabled.value.status_code == 403
    assert disabled.value.detail == "Answer API is disabled in agent-retrieval mode"