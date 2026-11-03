from fastapi.testclient import TestClient

from embedding_app.main import app


def test_embedding_health_does_not_require_model_download() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "design-rag-embedding"
    assert payload["dense"] is True
    assert payload["sparse"] is True
