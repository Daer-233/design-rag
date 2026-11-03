from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_evaluation_repository, get_rag_evaluation_service
from app.api.routes.rag_evaluations import router


class FakeRepository:
    def list_suites(self, **_: object) -> list[dict[str, object]]:
        return [{"id": "11111111-1111-1111-1111-111111111111", "name": "材料基准"}]


class FakeService:
    def capabilities(self) -> dict[str, object]:
        return {"evaluator": "ragas", "available": True, "metrics": ["faithfulness"]}


def app_with_overrides() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_evaluation_repository] = FakeRepository
    app.dependency_overrides[get_rag_evaluation_service] = FakeService
    return app


def test_rag_evaluation_capabilities_and_suite_listing() -> None:
    with TestClient(app_with_overrides()) as client:
        assert client.get("/api/v1/admin/rag-evaluations/capabilities").json()["evaluator"] == "ragas"
        response = client.get("/api/v1/admin/rag-evaluations/suites")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "材料基准"


def test_rag_evaluation_run_requires_explicit_external_approval() -> None:
    with TestClient(app_with_overrides()) as client:
        response = client.post(
            "/api/v1/admin/rag-evaluations/suites/11111111-1111-1111-1111-111111111111/runs",
            json={"explicit_external_evaluation_approved": False, "evidence_top_k": 6},
        )
    assert response.status_code == 422
    assert "explicit_external_evaluation_approved" in response.text
