from __future__ import annotations

from copy import deepcopy

import pytest

from app.providers.llm.base import GroundedAnswer
from app.services.rag_evaluation_service import RagEvaluationError, RagEvaluationService

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SUITE_ID = "22222222-2222-2222-2222-222222222222"


class FakeRepository:
    def __init__(self, cases: list[dict[str, object]], *, suite: bool = True) -> None:
        self.suite = {"id": SUITE_ID, "project_id": PROJECT_ID, "cases": cases} if suite else None
        self.enabled_cases = deepcopy(cases)
        self.runs: dict[str, dict[str, object]] = {}
        self.case_results: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []

    def get_suite(self, suite_id: str) -> dict[str, object] | None:
        assert suite_id == SUITE_ID
        return deepcopy(self.suite)

    def create_run(self, **kwargs: object) -> dict[str, object]:
        run = {"id": "33333333-3333-3333-3333-333333333333", "status": "queued", **kwargs}
        self.runs[str(run["id"])] = run
        return deepcopy(run)

    def claim_next_run(self) -> dict[str, object] | None:
        for run in self.runs.values():
            if run["status"] == "queued":
                run["status"] = "running"
                return deepcopy(run)
        return None

    def list_enabled_cases(self, suite_id: str) -> list[dict[str, object]]:
        assert suite_id == SUITE_ID
        return [deepcopy(item) for item in self.enabled_cases if item.get("enabled")]

    def store_case_result(self, **kwargs: object) -> None:
        self.case_results.append(dict(kwargs))

    def finish_run(self, **kwargs: object) -> None:
        self.finished.append(dict(kwargs))
        self.runs[str(kwargs["run_id"])].update(kwargs)

    def get_run(self, run_id: str) -> dict[str, object] | None:
        run = self.runs.get(run_id)
        if not run:
            return None
        return {**deepcopy(run), "case_results": deepcopy(self.case_results)}


class FakeSearchService:
    def search(self, **kwargs: object) -> dict[str, object]:
        if kwargs["query"] == "bad":
            raise RuntimeError("search unavailable")
        return {
            "results": [{
                "chunk_id": "chunk-1",
                "content": "绿城下沙项目的电视背景墙材料为深灰石材。",
                "citation": {"file_name": "主材表.pdf", "page_start": 1, "page_end": 1},
                "scores": {"rerank": 0.98},
            }],
        }


class FakeAnswerProvider:
    provider_name = "fake"

    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.calls: list[dict[str, object]] = []

    def is_configured(self) -> bool:
        return self.configured

    def answer_with_evidence(self, **kwargs: object) -> GroundedAnswer:
        self.calls.append(dict(kwargs))
        return GroundedAnswer(
            answer="电视背景墙为深灰石材。",
            citations=["S1"],
            insufficient_evidence=False,
            limitations=[],
            provider_response={},
        )


def case(question: str = "电视背景墙是什么材料？") -> dict[str, object]:
    return {"id": "44444444-4444-4444-4444-444444444444", "question": question,
            "reference_answer": "电视背景墙采用深灰石材。", "enabled": True, "sort_order": 0}


def test_capabilities_lists_safe_ragas_contract(monkeypatch) -> None:
    service = RagEvaluationService(FakeRepository([case()]), FakeSearchService(), FakeAnswerProvider())
    monkeypatch.setattr(service, "_ragas_version", lambda: ("0.3.9", True))

    capabilities = service.capabilities()

    assert capabilities["evaluator"] == "ragas"
    assert capabilities["metrics"] == ["faithfulness", "context_precision", "context_recall"]
    assert capabilities["external_submission"]["included"] == [
        "test_question", "reference_answer", "generated_answer", "retrieved_snippets"
    ]
    assert "api_key" not in str(capabilities).lower()


@pytest.mark.parametrize("configured, expected", [(False, "LLM gateway is not configured")])
def test_queue_run_requires_configured_gateway(monkeypatch, configured: bool, expected: str) -> None:
    service = RagEvaluationService(FakeRepository([case()]), FakeSearchService(), FakeAnswerProvider(configured))
    monkeypatch.setattr(service, "_ragas_version", lambda: ("0.3.9", True))

    with pytest.raises(RagEvaluationError, match=expected):
        service.queue_run(suite_id=SUITE_ID, evidence_top_k=6)


def test_queue_run_requires_enabled_case_and_available_ragas(monkeypatch) -> None:
    empty = RagEvaluationService(FakeRepository([{**case(), "enabled": False}]), FakeSearchService(), FakeAnswerProvider())
    with pytest.raises(RagEvaluationError, match="at least one enabled"):
        empty.queue_run(suite_id=SUITE_ID, evidence_top_k=6)

    unavailable = RagEvaluationService(FakeRepository([case()]), FakeSearchService(), FakeAnswerProvider())
    monkeypatch.setattr(unavailable, "_ragas_version", lambda: (None, False))
    with pytest.raises(RagEvaluationError, match="RAGAS is not available"):
        unavailable.queue_run(suite_id=SUITE_ID, evidence_top_k=6)


def test_run_next_queued_persists_scores_and_summary(monkeypatch) -> None:
    repository = FakeRepository([case()])
    service = RagEvaluationService(repository, FakeSearchService(), FakeAnswerProvider())
    monkeypatch.setattr(service, "_ragas_version", lambda: ("0.3.9", True))
    monkeypatch.setattr(service, "_build_ragas_llm", lambda: object())
    monkeypatch.setattr(service, "_score_with_ragas", lambda **_: {
        "faithfulness": 0.91, "context_precision": 0.82, "context_recall": 0.73,
    })

    queued = service.queue_run(suite_id=SUITE_ID, evidence_top_k=6)
    result = service.run_next_queued()

    assert queued["approval_record"]["explicit_external_evaluation_approved"] is True
    assert result["status"] == "succeeded"
    assert result["summary"]["scores"] == {
        "faithfulness": 0.91, "context_precision": 0.82, "context_recall": 0.73,
    }
    assert repository.case_results[0]["status"] == "succeeded"
    assert repository.case_results[0]["citations"][0]["citation"]["file_name"] == "主材表.pdf"


def test_one_case_failure_keeps_other_cases_and_marks_partial(monkeypatch) -> None:
    successful = case()
    failing = {**case("bad"), "id": "55555555-5555-5555-5555-555555555555", "sort_order": 1}
    repository = FakeRepository([successful, failing])
    service = RagEvaluationService(repository, FakeSearchService(), FakeAnswerProvider())
    monkeypatch.setattr(service, "_ragas_version", lambda: ("0.3.9", True))
    monkeypatch.setattr(service, "_build_ragas_llm", lambda: object())
    monkeypatch.setattr(service, "_score_with_ragas", lambda **_: {"faithfulness": 1.0})

    service.queue_run(suite_id=SUITE_ID, evidence_top_k=6)
    result = service.run_next_queued()

    assert result["status"] == "partially_succeeded"
    assert result["summary"]["succeeded_case_count"] == 1
    assert result["summary"]["failed_case_count"] == 1
    assert [item["status"] for item in repository.case_results] == ["succeeded", "failed"]
