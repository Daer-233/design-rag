from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes import search as search_route
from app.remote_main import app
from app.services.access_policy import Principal


class _FakeCatalog:
    def list_projects(self, principal: Principal) -> list[dict[str, object]]:
        assert principal.all_projects is True
        return [{"id": "11111111-1111-1111-1111-111111111111", "name": "01 绿城下沙", "external_key": "greentown-xiasha-01", "document_count": 3}]

    def list_project_documents(self, principal: Principal, project_id: str) -> list[dict[str, object]]:
        assert principal.all_projects is True
        return [{"id": "22222222-2222-2222-2222-222222222222", "project_id": project_id, "file_name": "材料表.pdf", "file_type": "pdf"}]

    def capabilities(self) -> dict[str, object]:
        return {"read_only": True, "tools": ["list_projects", "search_knowledge_base"]}


def _principal() -> Principal:
    return Principal(user_id="agent-all", project_ids=set(), roles=set(), all_projects=True)


def test_readonly_catalog_endpoints_are_token_scoped() -> None:
    app.dependency_overrides[search_route.get_readonly_search_principal] = _principal
    app.dependency_overrides[search_route.get_knowledge_catalog_repository] = _FakeCatalog
    client = TestClient(app)
    try:
        projects = client.get("/api/v1/projects")
        documents = client.get("/api/v1/projects/11111111-1111-1111-1111-111111111111/documents")
        capabilities = client.get("/api/v1/retrieval-capabilities")
    finally:
        app.dependency_overrides.clear()
    assert projects.status_code == 200
    assert projects.json()["projects"][0]["name"] == "01 绿城下沙"
    assert documents.status_code == 200
    assert documents.json()["documents"][0]["file_name"] == "材料表.pdf"
    assert capabilities.json()["read_only"] is True
