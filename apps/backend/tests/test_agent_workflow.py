from __future__ import annotations

from typing import Any

from app.providers.llm.base import GroundedAnswer
from app.services.agent_service import AgentService
from app.services.agent_workflow import DesignAgentWorkflow
from app.services.bert_intent_service import BertIntentClassifier, IntentResult


class FakeClassifier:
    def __init__(self, result: IntentResult) -> None:
        self.result = result
        self.questions: list[str] = []

    def classify(self, question: str) -> IntentResult:
        self.questions.append(question)
        return self.result


class FakeSearchService:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


class FakeAnswerProvider:
    provider_name = "fake-answer-provider"

    def __init__(self, *, configured: bool = False) -> None:
        self.configured = configured
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self.configured

    def answer_with_evidence(self, **kwargs: Any) -> GroundedAnswer:
        self.calls.append(kwargs)
        return GroundedAnswer("基于证据的回答", ["S1"], False, [], {"mode": "test"})


class FakeLlmClient:
    def __init__(self) -> None:
        self.rewrite_calls: list[dict[str, Any]] = []
        self.general_calls: list[dict[str, Any]] = []

    def rewrite_question(self, *, question: str, history: list[dict[str, Any]]) -> str:
        self.rewrite_calls.append({"question": question, "history": history})
        return "绿城下沙项目中木饰面用于哪些空间？"

    def general_answer(self, *, question: str, history: list[dict[str, Any]]) -> str:
        self.general_calls.append({"question": question, "history": history})
        return "这是模拟的日常回答。"


def _workflow(
    *,
    intent: str,
    retrieval: dict[str, Any] | None = None,
    answer_configured: bool = False,
) -> tuple[DesignAgentWorkflow, FakeSearchService, FakeAnswerProvider, FakeLlmClient]:
    search = FakeSearchService(retrieval or {"results": [], "pipeline": {}})
    provider = FakeAnswerProvider(configured=answer_configured)
    llm = FakeLlmClient()
    classifier = FakeClassifier(
        IntentResult(intent, 0.93, 0.93 if intent == "professional" else 0.07, 0.93 if intent == "daily" else 0.07, "fake-nli")
    )
    return (
        DesignAgentWorkflow(
            search_service=search,  # type: ignore[arg-type]
            answer_provider=provider,  # type: ignore[arg-type]
            classifier=classifier,  # type: ignore[arg-type]
            llm_client=llm,  # type: ignore[arg-type]
        ),
        search,
        provider,
        llm,
    )


def test_daily_route_never_queries_design_knowledge_base() -> None:
    workflow, search, _provider, llm = _workflow(intent="daily")
    events: list[tuple[str, str | None, dict[str, Any]]] = []

    state = workflow.invoke(
        query="帮我解释一下软装和硬装的区别",
        project_id=None,
        history=[],
        emit=lambda event_type, node_name, payload: events.append((event_type, node_name, payload)),
    )

    assert state["intent"] == "daily"
    assert state["content_origin"] == "general_llm"
    assert state["answer"] == "这是模拟的日常回答。"
    assert search.calls == []
    assert len(llm.general_calls) == 1
    assert any(event[1] == "prepare_general_answer" for event in events)


def test_complete_professional_question_keeps_original_text_and_skips_llm_without_evidence() -> None:
    workflow, search, provider, llm = _workflow(intent="professional")

    state = workflow.invoke(
        query="绿城下沙项目有哪些可确认的石材材料？",
        project_id="11111111-1111-1111-1111-111111111111",
        history=[],
        emit=lambda *_args: None,
    )

    assert state["intent"] == "professional"
    assert state["rewritten_query"] == "绿城下沙项目有哪些可确认的石材材料？"
    assert state["rewrite_reason"] == "original_question_complete"
    assert search.calls[0]["query"] == state["rewritten_query"]
    assert search.calls[0]["redact_source_details"] is True
    assert llm.rewrite_calls == []
    assert provider.calls == []
    assert state["content_origin"] == "knowledge_base"
    assert "没有检索到" in state["answer"]


def test_context_reference_is_rewritten_before_professional_retrieval() -> None:
    evidence = {
        "chunk_id": "child-1",
        "content": "木饰面用于会客区背景墙。",
        "parent_context": {"chunk_id": "page-1", "content": "材料说明"},
        "citation": {"file_name": "材料表.pdf", "file_type": "pdf", "page_start": 3, "page_end": 3},
        "scores": {"rerank": 0.88},
    }
    workflow, search, provider, llm = _workflow(intent="professional", retrieval={"results": [evidence], "pipeline": {}})

    state = workflow.invoke(
        query="它用在哪些空间？",
        project_id=None,
        history=[{"role": "user", "content": "绿城下沙项目有哪些木饰面？"}],
        emit=lambda *_args: None,
    )

    assert len(llm.rewrite_calls) == 1
    assert search.calls[0]["query"] == "绿城下沙项目中木饰面用于哪些空间？"
    assert provider.calls == []  # no external professional answer call while unconfigured
    assert state["citations"][0]["chunk_id"] == "child-1"
    assert state["content_origin"] == "knowledge_base"


def test_bert_fallback_keeps_two_way_routing_available_without_model_download(monkeypatch) -> None:
    classifier = BertIntentClassifier()

    def unavailable() -> tuple[Any, Any]:
        raise RuntimeError("modelscope offline for unit test")

    monkeypatch.setattr(classifier, "_ensure_model", unavailable)
    professional = classifier.classify("绿城下沙项目的木饰面型号是什么？")
    daily = classifier.classify("今天天气怎么样？")

    assert professional.label == "professional"
    assert daily.label == "daily"
    assert professional.fallback_reason
    assert daily.fallback_reason


def test_sse_event_has_replay_id_and_event_name() -> None:
    payload = AgentService.sse_event({"id": 42, "event_type": "retrieval.completed", "payload": {"summary": "ok"}})

    assert payload.startswith("id: 42\nevent: retrieval.completed\ndata: ")
    assert payload.endswith("\n\n")




def test_project_name_in_query_selects_unambiguous_project() -> None:
    projects = [
        {"id": "greentown-id", "name": "01 绿城下沙", "external_key": "greentown-xiasha-01"},
        {"id": "zhonghai-id", "name": "04 杭州中海", "external_key": "04-hang-zhou-zhong-hai"},
    ]

    greentown = AgentService._project_explicitly_named("绿城下沙有哪些木饰面材料？", projects)
    zhonghai = AgentService._project_explicitly_named("杭州中海的材料有哪些？", projects)
    comparison = AgentService._project_explicitly_named("对比绿城下沙和杭州中海的材料", projects)

    assert greentown is not None and greentown["id"] == "greentown-id"
    assert zhonghai is not None and zhonghai["id"] == "zhonghai-id"
    assert comparison is None

def test_duplicate_citations_are_collapsed_by_document_and_location() -> None:
    duplicate_document = {
        "project_name": "01 绿城下沙",
        "file_name": "主卧图.jpg",
        "citation": {
            "source_id": "document:green-bedroom",
            "project_name": "01 绿城下沙",
            "file_name": "主卧图.jpg",
            "file_type": "jpg",
        },
    }
    repeated_chunk = {**duplicate_document, "chunk_id": "another-child-chunk"}
    another_page = {
        **duplicate_document,
        "citation": {**duplicate_document["citation"], "page_start": 2, "page_end": 2},
    }

    collapsed = AgentService._deduplicate_citations([duplicate_document, repeated_chunk, another_page])

    assert collapsed == [duplicate_document, another_page]

def test_agent_project_catalog_uses_mcp_backed_lister() -> None:
    class FakeProjectCatalog:
        def list_projects(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "252f1120-cb29-4322-9ace-37dca1797413",
                    "name": "02 华润顶层会所",
                    "external_key": "crland-top-clubhouse",
                    "document_count": 8,
                }
            ]

    service = AgentService(
        repository=object(),  # type: ignore[arg-type]
        workflow=object(),  # type: ignore[arg-type]
        project_catalog=FakeProjectCatalog(),
    )

    assert service.list_projects() == [
        {
            "id": "252f1120-cb29-4322-9ace-37dca1797413",
            "name": "02 华润顶层会所",
            "external_key": "crland-top-clubhouse",
            "document_count": 8,
        }
    ]