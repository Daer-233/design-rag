"""LangGraph workflow for the DesignRAG web agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.providers.llm.base import AnswerProvider, AnswerProviderError
from app.services.access_policy import Principal
from app.services.answer_service import AnswerService
from app.services.bert_intent_service import BertIntentClassifier
from app.services.rag_service import SearchService

WorkflowEmitter = Callable[[str, str | None, dict[str, Any]], None]


class AgentState(TypedDict, total=False):
    query: str
    project_id: str | None
    history: list[dict[str, Any]]
    emit: WorkflowEmitter
    intent: Literal["professional", "daily"]
    intent_confidence: float
    intent_details: dict[str, Any]
    rewritten_query: str
    rewrite_reason: str
    retrieval: dict[str, Any]
    answer: str
    citations: list[dict[str, Any]]
    content_origin: str
    reasoning_summary: str


class AgentLlmClient:
    """Use the configured gateway for general chat and query rewriting."""

    def __init__(self) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.effective_llm_api_key
        self.model_id = settings.llm_model_id

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model_id)

    def chat(self, *, system: str, messages: list[dict[str, str]], max_tokens: int = 1200) -> str:
        if not self.is_configured():
            raise RuntimeError("LLM API is not configured")

        payload = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                raw = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"LLM gateway request failed: {type(error).__name__}") from error

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("LLM gateway response did not contain text") from error
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM gateway response was empty")
        return content.strip()

    def rewrite_question(self, *, question: str, history: list[dict[str, Any]]) -> str:
        history_text = "\n".join(
            f'{item.get("role", "user")}: {str(item.get("content", ""))[:500]}'
            for item in history[-6:]
        )
        result = self.chat(
            system=(
                "你负责将室内设计资料检索问题改写为独立、清晰、可检索的一句话。"
                "只消解已有对话中明确的指代；不要补充不存在的事实、不要回答问题。"
                "若原句已完整，原样输出。只输出改写后的问题。"
            ),
            messages=[{"role": "user", "content": f"对话上下文：\n{history_text}\n\n当前问题：{question}"}],
            max_tokens=220,
        )
        return result.replace("\n", " ").strip()[:1000]

    def general_answer(self, *, question: str, history: list[dict[str, Any]]) -> str:
        messages = [
            {"role": str(item.get("role", "user")), "content": str(item.get("content", ""))[:4000]}
            for item in history[-settings.agent_memory_message_limit:]
            if item.get("role") in {"user", "assistant"}
        ]
        messages.append({"role": "user", "content": question})
        return self.chat(
            system=(
                "你是 Design_RAG 的日常对话助手。使用中文简洁、自然地回答。"
                "当前问题未调用本地设计项目知识库，因此不要声称查阅过项目资料。"
                "不能确认的实时事实要明确说明；此初版暂不自动联网搜索。"
            ),
            messages=messages,
        )


class DesignAgentWorkflow:
    """Route a question through the existing retrieval or general-chat path."""

    def __init__(
        self,
        *,
        search_service: SearchService,
        answer_provider: AnswerProvider,
        classifier: BertIntentClassifier,
        llm_client: AgentLlmClient,
    ) -> None:
        self.search_service = search_service
        self.answer_provider = answer_provider
        self.classifier = classifier
        self.llm_client = llm_client
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("classify_intent", self._classify_intent)
        builder.add_node("rewrite_question", self._rewrite_question)
        builder.add_node("retrieve_knowledge", self._retrieve_knowledge)
        builder.add_node("prepare_grounded_answer", self._prepare_grounded_answer)
        builder.add_node("prepare_general_answer", self._prepare_general_answer)
        builder.add_edge(START, "classify_intent")
        builder.add_conditional_edges(
            "classify_intent",
            lambda state: state["intent"],
            {"professional": "rewrite_question", "daily": "prepare_general_answer"},
        )
        builder.add_edge("rewrite_question", "retrieve_knowledge")
        builder.add_edge("retrieve_knowledge", "prepare_grounded_answer")
        builder.add_edge("prepare_grounded_answer", END)
        builder.add_edge("prepare_general_answer", END)
        return builder.compile()

    def invoke(
        self, *, query: str, project_id: str | None, history: list[dict[str, Any]], emit: WorkflowEmitter
    ) -> AgentState:
        return self.graph.invoke({"query": query.strip(), "project_id": project_id, "history": history, "emit": emit})

    @staticmethod
    def _emit(state: AgentState, event_type: str, node_name: str, payload: dict[str, Any]) -> None:
        callback = state.get("emit")
        if callback:
            callback(event_type, node_name, payload)

    def _classify_intent(self, state: AgentState) -> dict[str, Any]:
        node = "classify_intent"
        self._emit(state, "workflow.node_started", node, {"label": "正在识别问题类型"})
        result = self.classifier.classify(state["query"])
        summary = (
            "识别为专业问题，将查询设计知识库。"
            if result.label == "professional"
            else "识别为日常问题，不查询设计知识库。"
        )
        payload = {
            "intent": result.label, "confidence": result.confidence,
            "classifier": result.classifier, "summary": summary,
        }
        if result.fallback_reason:
            payload["notice"] = "本地 BERT 正在准备中，当前使用临时本地规则路由。"
        self._emit(state, "intent.completed", node, payload)
        self._emit(state, "workflow.node_completed", node, {"summary": summary})
        return {"intent": result.label, "intent_confidence": result.confidence, "intent_details": payload}

    @staticmethod
    def _needs_rewrite(question: str, history: list[dict[str, Any]]) -> bool:
        references = ("这个", "那个", "上述", "前面", "刚才", "它", "他们", "第二个", "第一个", "该方案")
        return bool(history and any(token in question for token in references)) or len(question) > 100

    def _rewrite_question(self, state: AgentState) -> dict[str, Any]:
        node = "rewrite_question"
        query = state["query"]
        history = state.get("history", [])
        self._emit(state, "workflow.node_started", node, {"label": "正在检查问题是否需要重构"})
        rewritten = query
        if self._needs_rewrite(query, history):
            try:
                rewritten = self.llm_client.rewrite_question(question=query, history=history) or query
                summary = "已结合本轮对话上下文重构检索问题。"
                reason = "context_resolution"
            except RuntimeError as error:
                summary = "问题重构服务暂不可用，已使用原句继续检索。"
                reason = f"fallback:{type(error).__name__}"
        else:
            summary = "问题表达完整，保留原句检索。"
            reason = "original_question_complete"
        self._emit(state, "rewrite.completed", node, {"rewritten": rewritten != query, "summary": summary})
        self._emit(state, "workflow.node_completed", node, {"summary": summary})
        return {"rewritten_query": rewritten, "rewrite_reason": reason}

    def _retrieve_knowledge(self, state: AgentState) -> dict[str, Any]:
        node = "retrieve_knowledge"
        self._emit(state, "retrieval.started", node, {"label": "正在检索知识库"})
        principal = Principal(user_id="anonymous-local", project_ids=set(), roles=set(), all_projects=True)
        retrieval = self.search_service.search(
            query=state.get("rewritten_query") or state["query"],
            project_id=state.get("project_id"),
            principal_raw=principal,
            evidence_top_k=settings.retrieval_evidence_top_k,
            retrieval_filters=None,
            redact_source_details=True,
        )
        pipeline = retrieval.get("pipeline", {}) if isinstance(retrieval, dict) else {}
        result_count = len(retrieval.get("results", [])) if isinstance(retrieval, dict) else 0
        summary = f"已完成召回、粗排序与重排序，保留 {result_count} 条证据。"
        self._emit(state, "retrieval.completed", node, {
            "summary": summary,
            "evidence_count": result_count,
            "pipeline": {key: pipeline.get(key) for key in ("recall_top_k", "coarse_top_k", "rerank_top_k")},
        })
        self._emit(state, "workflow.node_completed", node, {"summary": summary})
        return {"retrieval": retrieval}

    def _prepare_grounded_answer(self, state: AgentState) -> dict[str, Any]:
        node = "prepare_grounded_answer"
        self._emit(state, "workflow.node_started", node, {"label": "正在生成基于证据的专业回答"})
        retrieval = state.get("retrieval") or {}
        evidence = retrieval.get("results", []) if isinstance(retrieval, dict) else []
        result: dict[str, Any] = {"content_origin": "knowledge_base", "citations": []}
        if not evidence:
            self._emit(state, "evidence.selected", node, {
                "evidence_count": 0, "summary": "没有命中可用证据，因此未调用外部回答模型。",
            })
            return {
                **result,
                "answer": "当前已索引资料中没有检索到足以回答该问题的证据，暂时无法确认。",
                "reasoning_summary": "已完成专业问题检索，但当前资料证据不足。",
            }

        packet, source_map, _truncation = AnswerService._evidence_packet(evidence)
        self._emit(state, "evidence.selected", node, {
            "evidence_count": len(source_map), "summary": "已选择经过重排序的证据，并回填父块上下文。",
        })
        result["citations"] = list(source_map.values())
        if not self.answer_provider.is_configured():
            return {
                **result,
                "answer": "已检索到相关证据，但专业回答模型尚未配置。请查看下方引用证据。",
                "reasoning_summary": "已检索并选择证据；回答模型未配置。",
            }

        try:
            generated = self.answer_provider.answer_with_evidence(
                question=state.get("rewritten_query") or state["query"],
                evidence_packet=packet,
                allowed_citation_ids=list(source_map),
                prompt_version=settings.answer_prompt_version,
            )
        except AnswerProviderError as error:
            self._emit(state, "workflow.notice", node, {"summary": f"回答模型暂不可用：{type(error).__name__}"})
            return {
                **result,
                "answer": "已检索到相关证据，但生成专业回答时出现问题。请查看下方引用证据。",
                "reasoning_summary": "已检索并选择证据；专业回答生成未完成。",
            }

        summary = "回答仅基于已检索证据生成，并附带可追溯引用。"
        self._emit(state, "workflow.node_completed", node, {"summary": summary})
        return {
            "answer": generated.answer,
            "citations": [source_map[key] for key in generated.citations if key in source_map],
            "content_origin": "knowledge_base",
            "reasoning_summary": summary,
        }

    def _prepare_general_answer(self, state: AgentState) -> dict[str, Any]:
        node = "prepare_general_answer"
        self._emit(state, "workflow.node_started", node, {"label": "正在生成日常回答"})
        try:
            answer = self.llm_client.general_answer(question=state["query"], history=state.get("history", []))
        except RuntimeError:
            answer = "日常回答模型暂未配置或当前不可用。请检查 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL_ID 后再试。"
        summary = "本轮识别为日常问题，未查询本地设计项目知识库。"
        self._emit(state, "workflow.node_completed", node, {"summary": summary})
        return {"answer": answer, "citations": [], "content_origin": "general_llm", "reasoning_summary": summary}
