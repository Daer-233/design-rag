"""Exercise the full search path without models, databases or HTTP requests."""

from types import SimpleNamespace

import pytest

from app.clients.inference import InferenceServiceError
from app.core.config import Settings
from app.providers.vector.base import VectorCandidate
from app.services import rag_service
from app.services.access_policy import AccessDeniedError, AccessPolicy, Principal

PROJECT = "11111111-1111-1111-1111-111111111111"


def make_service(monkeypatch, *, count=40, budget=20, denied=False, fail=False):
    config = Settings(_env_file=None, retrieval_rerank_candidate_top_k=budget,
                      retrieval_rerank_top_k=6, retrieval_evidence_top_k=8)
    monkeypatch.setattr(rag_service, "settings", config)
    rows = {}
    for i in range(count):
        rows[f"c{i}"] = {
            "chunk_id": f"c{i}", "content": f"evidence {i}", "project_id": PROJECT,
            "document_id": f"d{i // 2}", "chunk_level": "child", "parent_id": None,
            "chunk_access_scope": {}, "parent_access_scope": {},
            "chunk_metadata": {}, "review_status": "approved", "source_confidence": 1.0,
            "project_name": "fixture", "project_external_key": "fixture",
            "file_name": f"d{i // 2}.pdf", "file_type": "pdf", "chunk_type": "text",
            "page_start": i + 1, "page_end": i + 1, "section_path": None,
            "source_uri": "private-path", "source_locator": {"page": i + 1},
        }
    if denied:
        rows["c0"]["chunk_access_scope"] = {"roles": ["admin"]}
        rows["c1"]["parent_access_scope"] = {"roles": ["admin"]}
        rows["c2"]["project_id"] = "22222222-2222-2222-2222-222222222222"
    calls = {"vector": [], "rerank": []}

    def hybrid_search(**kwargs):
        calls["vector"].append(kwargs)
        return [VectorCandidate(f"c{i}", 1.0, i + 1, 1.0, i + 1, 1 / (i + 1))
                for i in range(count)]

    def rerank(query, documents):
        calls["rerank"].append(documents)
        if fail:
            raise InferenceServiceError("rerank unavailable")
        # The most relevant child is below coarse rank six.
        return [{"index": i, "score": 10.0 if text == "evidence 12" else 1 / (i + 1)}
                for i, text in enumerate(documents)]

    monkeypatch.setattr(rag_service, "get_vector_provider", lambda: SimpleNamespace(hybrid_search=hybrid_search))
    service = rag_service.SearchService(
        SimpleNamespace(project_exists=lambda _: True, fetch_chunks=lambda ids: rows),
        SimpleNamespace(embed=lambda _: {"dense_vectors": [[1.0]],
                                        "sparse_vectors": [{"indices": [1], "values": [1.0]}]}),
        SimpleNamespace(rerank=rerank), AccessPolicy(),
    )
    return service, calls


def search(service, k=8, project=PROJECT):
    return service.search(query="materials", project_id=project,
                          principal_raw=Principal("tester", {PROJECT}, {"designer"}),
                          evidence_top_k=k, include_images=False, redact_source_details=True)


@pytest.mark.parametrize("budget,expected", [(6, 6), (8, 8), (20, 8), (40, 8)])
def test_candidate_budget_and_final_evidence_are_independent(monkeypatch, budget, expected):
    service, calls = make_service(monkeypatch, budget=budget)
    result = search(service)
    assert len(calls["rerank"][0]) == budget
    assert len(result["results"]) == expected
    assert result["pipeline"]["rerank_input_count"] == budget
    assert result["pipeline"]["evidence_shortfall"] == 8 - expected
    assert result["pipeline"]["rerank_top_k"] == budget
    if budget >= 20:
        assert result["results"][0]["chunk_id"] == "c12"
        assert result["results"][0]["scores"]["rerank"] == 10.0
        assert result["results"][0]["citation"]["page_start"] == 13
    assert all("source_uri" not in item["citation"] for item in result["results"])


def test_requesting_more_evidence_does_not_change_model_input_budget(monkeypatch):
    service, calls = make_service(monkeypatch, budget=20)
    assert len(search(service, k=12)["results"]) == 12
    assert len(calls["rerank"][0]) == 20


def test_short_corpus_reports_shortfall(monkeypatch):
    service, calls = make_service(monkeypatch, count=3)
    result = search(service)
    assert len(calls["rerank"][0]) == 3
    assert result["pipeline"]["evidence_shortfall"] == 5


def test_unauthorized_children_parents_and_other_projects_never_reach_reranker(monkeypatch):
    service, calls = make_service(monkeypatch, denied=True)
    result = search(service)
    assert not {"evidence 0", "evidence 1", "evidence 2"}.intersection(calls["rerank"][0])
    assert not {"c0", "c1", "c2"}.intersection(item["chunk_id"] for item in result["results"])
    assert calls["vector"][0]["search_filter"].principal_roles == ["designer"]
    assert calls["vector"][0]["search_filter"].chunk_level == "child"


def test_project_denied_before_model_or_vector_calls(monkeypatch):
    service, calls = make_service(monkeypatch)
    with pytest.raises(AccessDeniedError):
        search(service, project="22222222-2222-2222-2222-222222222222")
    assert calls == {"vector": [], "rerank": []}


def test_rerank_failure_does_not_return_unscored_evidence(monkeypatch):
    service, _ = make_service(monkeypatch, fail=True)
    with pytest.raises(InferenceServiceError):
        search(service)


def test_final_document_cap_still_applies(monkeypatch):
    service, _ = make_service(monkeypatch)
    result = search(service)
    ids = [item["citation"]["document_id"] for item in result["results"]]
    assert all(ids.count(document_id) <= 2 for document_id in ids)


def test_new_budget_precedes_legacy_and_legacy_only_still_works(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_RERANK_TOP_K", "6")
    monkeypatch.delenv("RETRIEVAL_RERANK_CANDIDATE_TOP_K", raising=False)
    assert Settings(_env_file=None).effective_rerank_candidate_top_k == 6
    monkeypatch.setenv("RETRIEVAL_RERANK_CANDIDATE_TOP_K", "20")
    assert Settings(_env_file=None).effective_rerank_candidate_top_k == 20
    monkeypatch.delenv("RETRIEVAL_RERANK_TOP_K")
    monkeypatch.delenv("RETRIEVAL_RERANK_CANDIDATE_TOP_K")
    assert Settings(_env_file=None).effective_rerank_candidate_top_k == 8


@pytest.mark.parametrize("budget", [0, 101])
def test_candidate_budget_is_bounded(budget):
    with pytest.raises(ValueError):
        Settings(_env_file=None, retrieval_rerank_candidate_top_k=budget)
