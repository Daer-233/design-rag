from fastapi.testclient import TestClient

from rerank_app.main import app


def test_rerank_health_does_not_require_model_download() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "design-rag-rerank"
