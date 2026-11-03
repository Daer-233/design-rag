from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.api.schemas import AnswerRequest
from app.providers.llm.base import AnswerProviderError, GroundedAnswer
from app.providers.llm.openai_compatible_answer import OpenAICompatibleAnswerProvider
from app.services.answer_service import AnswerService


PROJECT_ID = str(uuid4())


def _evidence() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "child-1",
            "content": "环保板材适用于本项目的客厅柜体。",
            "parent_context": {
                "chunk_id": "parent-1",
                "content": "材料建议：客厅柜体优先选择环保板材，并结合现场尺寸复核。",
            },
            "citation": {
                "file_name": "材料说明.docx",
                "file_type": "docx",
                "page_start": 3,
                "page_end": 3,
                "section_path": "材料 / 柜体",
                "source_uri": "I:/Design_RAG/test-data/material.docx",
            },
            "scores": {"coarse": 0.8, "rerank": 0.9},
        }
    ]


class FakeSearchService:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    def search(self, **_: Any) -> dict[str, Any]:
        return {
            "query": {"raw": "材料怎么选", "effective": "材料怎么选", "rewritten": False},
            "project_id": PROJECT_ID,
            "pipeline": {"performance_ms": {"total": 1.0}},
            "results": self.results,
        }


class FakeAnswerProvider:
    provider_name = "fake-answer-provider"

    def __init__(self, generated: GroundedAnswer) -> None:
        self.generated = generated
        self.calls: list[dict[str, Any]] = []

    def answer_with_evidence(self, **kwargs: Any) -> GroundedAnswer:
        self.calls.append(kwargs)
        return self.generated


def test_answer_service_sends_only_selected_evidence_and_maps_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeAnswerProvider(
        GroundedAnswer(
            answer="建议使用环保板材。[S1]",
            citations=["S1"],
            insufficient_evidence=False,
            limitations=[],
            provider_response={"id": "local-test"},
        )
    )
    service = AnswerService(FakeSearchService(_evidence()), provider)  # type: ignore[arg-type]

    result = service.answer(
        query="材料怎么选",
        project_id=PROJECT_ID,
        principal_raw=object(),
        evidence_top_k=1,
    )

    assert result["citations"][0]["source_id"] == "S1"
    assert result["provider"]["name"] == "fake-answer-provider"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["question"] == "材料怎么选"
    assert "环保板材" in call["evidence_packet"]
    assert "I:/Design_RAG/test-data/material.docx" not in call["evidence_packet"]
    assert "source_uri" not in call["evidence_packet"]
    assert "api_key" not in call["evidence_packet"].lower()


def test_answer_service_does_not_call_provider_without_evidence() -> None:
    provider = FakeAnswerProvider(
        GroundedAnswer("should not be used", ["S1"], False, [], {})
    )
    service = AnswerService(FakeSearchService([]), provider)  # type: ignore[arg-type]

    result = service.answer(
        query="没有资料的问题",
        project_id=PROJECT_ID,
        principal_raw=object(),
        evidence_top_k=None,
    )

    assert result["insufficient_evidence"] is True
    assert result["pipeline"]["generation"]["called"] is False
    assert provider.calls == []


def test_evidence_packet_truncates_child_parent_and_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.answer_service.settings.answer_evidence_child_max_chars", 256)
    monkeypatch.setattr("app.services.answer_service.settings.answer_evidence_parent_max_chars", 256)
    monkeypatch.setattr("app.services.answer_service.settings.answer_evidence_total_max_chars", 1000)
    packet, source_map, truncation = AnswerService._evidence_packet(
        [
            {
                "chunk_id": "child-1",
                "content": "C" * 1000,
                "parent_context": {"chunk_id": "parent-1", "content": "P" * 1000},
                "citation": {"file_name": "a.pdf", "file_type": "pdf"},
            },
            {
                "chunk_id": "child-2",
                "content": "D" * 1000,
                "parent_context": None,
                "citation": {"file_name": "b.pdf", "file_type": "pdf"},
            },
        ]
    )

    assert source_map
    assert len(packet) <= 1000 + 20
    assert truncation["truncated_source_ids"]
    assert isinstance(truncation["omitted_source_ids"], list)


def test_answer_provider_rejects_unknown_citation() -> None:
    with pytest.raises(AnswerProviderError, match="outside the supplied evidence"):
        OpenAICompatibleAnswerProvider._parse_answer(
            json.dumps(
                {
                    "answer": "结论",
                    "citations": ["S9"],
                    "insufficient_evidence": False,
                    "limitations": [],
                }
            ),
            {"S1"},
        )


def test_answer_provider_request_contains_no_source_path_or_token() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "id": "local-mock",
                "model": "gpt-5.6-terra",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "建议使用环保板材。[S1]",
                                    "citations": ["S1"],
                                    "insufficient_evidence": False,
                                    "limitations": [],
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAICompatibleAnswerProvider(
        base_url="https://mock.test/v1",
        api_key="REPLACE_ME",
        model_id="gpt-5.6-terra",
        transport=httpx.MockTransport(handler),
    )
    result = provider.answer_with_evidence(
        question="材料怎么选",
        evidence_packet="[S1] 文件：材料说明.docx\n子块证据：环保板材适用于客厅柜体。",
        allowed_citation_ids=["S1"],
        prompt_version="test-v1",
    )

    assert result.citations == ["S1"]
    body_text = json.dumps(captured["body"], ensure_ascii=False)
    assert "I:/Design_RAG" not in body_text
    assert "source_uri" not in body_text
    assert "local-secret-token" not in body_text
    assert captured["authorization"] == "Bearer local-secret-token"


def test_answer_request_requires_explicit_export_approval() -> None:
    base = {
        "query": "材料怎么选",
        "project_id": PROJECT_ID,
        "principal": {"user_id": "u1", "project_ids": [PROJECT_ID], "roles": []},
    }
    with pytest.raises(ValidationError):
        AnswerRequest.model_validate(base)
    payload = AnswerRequest.model_validate({**base, "explicit_external_export_approved": True})
    assert payload.explicit_external_export_approved is True

