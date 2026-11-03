from fastapi.testclient import TestClient

from app.api.routes import search as search_route
from app.remote_main import app
from app.services.access_policy import Principal


class _FakeSearchService:
    def search(self, **_: object) -> dict[str, object]:
        return {
            "query": {"raw": "测试问题", "effective": "测试问题", "rewritten": False},
            "project_id": "11111111-1111-1111-1111-111111111111",
            "pipeline": {},
            "results": [
                {"chunk_id": "child-1", "content": "evidence one"},
                {"chunk_id": "child-2", "content": "evidence two"},
            ],
        }


class _FakeAuditService:
    def __init__(self) -> None:
        self.items: list[object] = []

    def record_safely(self, item: object) -> None:
        self.items.append(item)


def test_search_audit_uses_authenticated_agent_and_safe_chunk_ids(monkeypatch) -> None:
    audit = _FakeAuditService()
    principal = Principal(
        user_id="agent-default",
        project_ids={"11111111-1111-1111-1111-111111111111"},
        roles={"designer"},
    )
    monkeypatch.setattr(search_route, "get_search_service", lambda: _FakeSearchService())
    monkeypatch.setattr(search_route, "get_search_audit_service", lambda: audit)
    app.dependency_overrides[search_route.get_readonly_search_principal] = lambda: principal
    try:
        response = TestClient(app).post(
            "/api/v1/search",
            json={"query": "测试问题", "project_id": "11111111-1111-1111-1111-111111111111"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(audit.items) == 1
    item = audit.items[0]
    assert item.actor_id == "agent-default"
    assert item.actor_display_name is None
    assert item.identity_source == "token_principal"
    assert item.result_chunk_ids == ["child-1", "child-2"]
    assert item.query_text == "测试问题"
