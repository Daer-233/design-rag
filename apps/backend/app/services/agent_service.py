"""Application service for anonymous Agent conversations and persisted SSE events."""

from __future__ import annotations

from collections.abc import Iterator
import re
import time
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.search_audit_repository import SearchAuditWrite
from app.services.agent_workflow import DesignAgentWorkflow
from app.services.search_audit_service import SearchAuditService


class AgentService:
    def __init__(
        self,
        repository: AgentRepository,
        workflow: DesignAgentWorkflow,
        search_audit_service: SearchAuditService | None = None,
        project_catalog: object | None = None,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.search_audit_service = search_audit_service
        self.project_catalog = project_catalog or repository

    def ensure_client(self, anonymous_client_id: str) -> str:
        return self.repository.touch_anonymous_client(anonymous_client_id)

    def create_conversation(self, anonymous_client_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        return self.repository.create_conversation(anonymous_client_id, project_id=project_id)

    def list_conversations(self, anonymous_client_id: str) -> list[dict[str, Any]]:
        return self.repository.list_conversations(anonymous_client_id)

    def get_conversation(self, anonymous_client_id: str, conversation_id: str) -> dict[str, Any] | None:
        return self.repository.get_conversation(anonymous_client_id, conversation_id)

    def list_messages(self, anonymous_client_id: str, conversation_id: str) -> list[dict[str, Any]]:
        return self.repository.list_messages(anonymous_client_id, conversation_id)

    def _list_retrievable_projects(self) -> list[dict[str, Any]]:
        lister = getattr(self.project_catalog, "list_projects", None)
        if not callable(lister):
            return self.repository.list_retrievable_projects()
        projects = lister()
        return [dict(item) for item in projects if isinstance(item, dict)]

    def list_projects(self) -> list[dict[str, Any]]:
        """Expose the same MCP-backed project catalog used to resolve Chat runs."""
        return self._list_retrievable_projects()

    @staticmethod
    def _project_aliases(project: dict[str, Any]) -> set[str]:
        """Normalize project labels so a human project name can safely scope retrieval."""
        name = str(project.get("name") or "").strip()
        external_key = str(project.get("external_key") or "").strip()
        aliases = {name, external_key}
        if name:
            aliases.add(re.sub(r"^\s*\d+\s*", "", name))
        return {
            re.sub(r"[\s_\-]+", "", alias).casefold()
            for alias in aliases
            if len(alias.strip()) >= 2
        }

    @classmethod
    def _project_explicitly_named(
        cls,
        query: str,
        projects: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Resolve one unambiguous project mentioned in a query; keep comparisons global."""
        compact_query = re.sub(r"[\s_\-]+", "", query).casefold()
        matches = [
            project
            for project in projects
            if any(alias in compact_query for alias in cls._project_aliases(project))
        ]
        return matches[0] if len(matches) == 1 else None

    def start_run(
        self,
        *,
        anonymous_client_id: str,
        conversation_id: str,
        query: str,
        project_id: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        conversation = self.repository.get_conversation(anonymous_client_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("Message cannot be empty")
        if request_id:
            existing = self.repository.find_run_by_request_id(anonymous_client_id, conversation_id, request_id)
            if existing is not None:
                return {"run": existing, "reused": True}
        selected_project_id = project_id or conversation.get("project_id")
        named_project = self._project_explicitly_named(
            cleaned_query,
            self._list_retrievable_projects(),
        )
        if named_project is not None:
            selected_project_id = str(named_project["id"])
            if str(conversation.get("project_id") or "") != selected_project_id:
                self.repository.set_conversation_project(conversation_id, selected_project_id)

        run = self.repository.create_run(
            conversation_id=conversation_id,
            query_text=cleaned_query,
            project_id=selected_project_id,
            request_id=request_id,
        )
        self.repository.append_message(
            conversation_id=conversation_id,
            run_id=str(run["id"]),
            role="user",
            content=cleaned_query,
            content_origin="user",
        )
        self.repository.set_conversation_title_if_default(conversation_id, cleaned_query)
        if named_project is not None:
            self.repository.add_event(
                run_id=str(run["id"]),
                event_type="workflow.notice",
                node_name="resolve_project_scope",
                payload={
                    "summary": (
                        f"检测到问题明确提及「{named_project['name']}」，本轮仅检索该项目资料。"
                    )
                },
            )
        self.repository.add_event(
            run_id=str(run["id"]),
            event_type="run.started",
            node_name="agent",
            payload={"summary": "已收到问题，正在准备工作流。"},
        )
        return {"run": run, "reused": False}

    @staticmethod
    def _citation_identity(raw_citation: Any) -> tuple[str, ...] | None:
        """Identify a user-visible source while allowing distinct pages/sections to remain separate."""
        if not isinstance(raw_citation, dict):
            return None
        nested = raw_citation.get("citation")
        nested = nested if isinstance(nested, dict) else {}

        def first_text(*values: Any) -> str:
            for value in values:
                if value is not None and str(value).strip():
                    return str(value).strip()
            return ""

        source = first_text(
            nested.get("document_id"),
            raw_citation.get("document_id"),
            nested.get("source_id"),
            raw_citation.get("source_id"),
        )
        file_name = first_text(nested.get("file_name"), raw_citation.get("file_name"))
        project = first_text(
            nested.get("project_id"),
            raw_citation.get("project_id"),
            nested.get("project_name"),
            raw_citation.get("project_name"),
        )
        page_start = first_text(nested.get("page_start"), raw_citation.get("page_start"))
        page_end = first_text(nested.get("page_end"), raw_citation.get("page_end"))
        section = first_text(nested.get("section_path"), raw_citation.get("section_path"))
        if not any((source, file_name, project)):
            return None
        return (project.casefold(), source.casefold(), file_name.casefold(), page_start, page_end, section.casefold())

    @classmethod
    def _deduplicate_citations(cls, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse repeated retrieval chunks that resolve to the same displayed citation."""
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for citation in citations:
            identity = cls._citation_identity(citation)
            if identity is not None and identity in seen:
                continue
            if identity is not None:
                seen.add(identity)
            unique.append(citation)
        return unique
    @classmethod
    def _deduplicate_images(cls, images: list[dict[str, Any]], *, run_id: str, client_id: str) -> list[dict[str, Any]]:
        """Return bounded Chat image references bound to this run and anonymous client."""
        from app.services.image_asset_access import sign_asset_access

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in images:
            if not isinstance(raw, dict):
                continue
            asset_id = str(raw.get("asset_id") or "").strip()
            if not asset_id or asset_id in seen:
                continue
            project_id = str(raw.get("project_id") or "").strip() or None
            if not project_id:
                citation = raw.get("citation") if isinstance(raw.get("citation"), dict) else {}
                project_id = str(citation.get("project_id") or "").strip() or None
            token = sign_asset_access(asset_id, project_id, run_id=run_id, client_id=client_id)
            query = f"?access_token={token}&run_id={run_id}"
            item = dict(raw)
            prefix = f"/api/v1/chat/assets/{asset_id}"
            item.update(
                {
                    "asset_id": asset_id,
                    "thumbnail_url": f"{prefix}/thumbnail{query}",
                    "preview_url": f"{prefix}/preview{query}",
                    "original_url": f"{prefix}/original{query}",
                    "resource_uri": f"design-rag://assets/{asset_id}",
                }
            )
            unique.append(item)
            seen.add(asset_id)
            if len(unique) >= 8:
                break
        return unique

    @staticmethod
    def _collect_images(state: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect top-level and per-result image references from MCP/Search state."""
        collected: list[dict[str, Any]] = []
        candidates: list[Any] = [state.get("images")]
        for key in ("results", "evidence", "retrieval"):
            value = state.get(key)
            if isinstance(value, dict):
                candidates.append(value.get("images"))
                candidates.append(value.get("results"))
            else:
                candidates.append(value)
        for value in candidates:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("asset_id"):
                        collected.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("images"), list):
                        collected.extend(x for x in item["images"] if isinstance(x, dict))
        return collected
    @staticmethod
    def _audit_chunk_ids(citations: list[dict[str, Any]]) -> list[str]:
        """Return stable, unique evidence IDs without exposing source content to the audit table."""
        return list(dict.fromkeys(
            str(item["chunk_id"])
            for item in citations
            if isinstance(item, dict) and item.get("chunk_id") is not None
        ))

    def _record_agent_retrieval_audit(
        self,
        *,
        run: dict[str, Any],
        anonymous_client_id: str,
        state: dict[str, Any] | None,
        outcome: str,
        error_code: str | None,
        started: float,
    ) -> None:
        if self.search_audit_service is None:
            return
        raw_citations = list((state or {}).get("citations") or [])
        retrieval_query = str((state or {}).get("rewritten_query") or run.get("query_text") or "")
        self.search_audit_service.record_safely(
            SearchAuditWrite(
                actor_id=f"chat:{anonymous_client_id}",
                actor_display_name="Chat 匿名访客",
                identity_source="anonymous_agent",
                principal_roles=["anonymous_agent"],
                project_id=str(run["project_id"]) if run.get("project_id") else None,
                query_text=str(run.get("query_text") or ""),
                retrieval_query_text=retrieval_query,
                requested_evidence_top_k=settings.agent_evidence_top_k,
                result_chunk_ids=self._audit_chunk_ids(raw_citations),
                outcome=outcome,
                error_code=error_code,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                client_ip=None,
                client_user_agent=None,
                agent_run_id=str(run["id"]),
                conversation_id=str(run["conversation_id"]),
            )
        )

    def execute_run(self, *, anonymous_client_id: str, run_id: str) -> None:
        run = self.repository.get_run(anonymous_client_id, run_id)
        if run is None or run.get("status") != "queued":
            return
        self.repository.mark_run_running(run_id)
        self.repository.add_event(
            run_id=run_id,
            event_type="workflow.node_started",
            node_name="load_memory",
            payload={"summary": "正在加载本会话的近期对话记忆。"},
        )
        messages = self.repository.list_messages(anonymous_client_id, str(run["conversation_id"]))
        history = messages[-(settings.agent_memory_message_limit + 1) :]
        if history and history[-1].get("role") == "user" and history[-1].get("content") == run.get("query_text"):
            history = history[:-1]
        self.repository.add_event(
            run_id=run_id,
            event_type="workflow.node_completed",
            node_name="load_memory",
            payload={"summary": f"已加载 {len(history)} 条近期对话记录。"},
        )

        retrieval_started_at: float | None = None
        retrieval_observed = False

        def emit(event_type: str, node_name: str | None, payload: dict[str, Any]) -> None:
            nonlocal retrieval_started_at, retrieval_observed
            if event_type == "retrieval.started":
                retrieval_started_at = time.perf_counter()
                retrieval_observed = True
            elif event_type == "retrieval.completed":
                retrieval_observed = True
            self.repository.add_event(run_id=run_id, event_type=event_type, node_name=node_name, payload=payload)

        try:
            state = self.workflow.invoke(
                query=str(run["query_text"]),
                project_id=str(run["project_id"]) if run.get("project_id") else None,
                history=history,
                emit=emit,
            )
            answer = str(state.get("answer") or "暂时未生成回答。")
            raw_citations = list(state.get("citations") or [])
            citations = self._deduplicate_citations(raw_citations)
            images = self._deduplicate_images(self._collect_images(state), run_id=run_id, client_id=anonymous_client_id)
            if images:
                self.repository.add_event(
                    run_id=run_id,
                    event_type="retrieval.images",
                    node_name="retrieve_images",
                    payload={"images": images},
                )
            if retrieval_observed:
                self._record_agent_retrieval_audit(
                    run=run,
                    anonymous_client_id=anonymous_client_id,
                    state=state,
                    outcome="succeeded",
                    error_code=None,
                    started=retrieval_started_at or time.perf_counter(),
                )
            if len(citations) < len(raw_citations):
                self.repository.add_event(
                    run_id=run_id,
                    event_type="workflow.notice",
                    node_name="deduplicate_citations",
                    payload={
                        "summary": f"已合并 {len(raw_citations) - len(citations)} 条重复引用，按来源、页码和章节展示证据。"
                    },
                )
            for start in range(0, len(answer), settings.agent_stream_chunk_chars):
                current = self.repository.get_run(anonymous_client_id, run_id)
                if current is None or current.get("status") == "cancelled":
                    self.repository.add_event(
                        run_id=run_id,
                        event_type="run.cancelled",
                        node_name="agent",
                        payload={"summary": "已停止生成。"},
                    )
                    return
                self.repository.add_event(
                    run_id=run_id,
                    event_type="answer.token",
                    node_name="stream_answer",
                    payload={"text": answer[start : start + settings.agent_stream_chunk_chars]},
                )
                if start + settings.agent_stream_chunk_chars < len(answer):
                    time.sleep(settings.agent_stream_chunk_interval_seconds)
            self.repository.add_event(
                run_id=run_id,
                event_type="answer.citation",
                node_name="stream_answer",
                payload={"citations": citations, "images": images},
            )
            self.repository.append_message(
                conversation_id=str(run["conversation_id"]),
                run_id=run_id,
                role="assistant",
                content=answer,
                content_origin=str(state.get("content_origin") or "general_llm"),
                citations=citations,
                metadata={
                    "intent": state.get("intent"),
                    "rewritten_query": state.get("rewritten_query"),
                    "reasoning_summary": state.get("reasoning_summary"),
                    "images": images,
                },
            )
            self.repository.complete_run(
                run_id,
                intent_label=str(state.get("intent") or "daily"),
                intent_confidence=float(state.get("intent_confidence") or 0.5),
                rewritten_query=str(state.get("rewritten_query") or run["query_text"]),
                reasoning_summary=str(state.get("reasoning_summary") or "已完成本轮工作流。"),
            )
            self.repository.add_event(
                run_id=run_id,
                event_type="answer.completed",
                node_name="stream_answer",
                payload={
                    "summary": "回答生成完成。",
                    "content_origin": str(state.get("content_origin") or "general_llm"),
                    "intent": state.get("intent"),
                    "rewritten_query": state.get("rewritten_query"),
                    "reasoning_summary": state.get("reasoning_summary"),
                    "images": images,
                },
            )
            self.repository.add_event(
                run_id=run_id,
                event_type="run.completed",
                node_name="agent",
                payload={"summary": "本轮工作流完成。"},
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if retrieval_observed:
                self._record_agent_retrieval_audit(
                    run=run,
                    anonymous_client_id=anonymous_client_id,
                    state=None,
                    outcome="failed",
                    error_code=type(error).__name__,
                    started=retrieval_started_at or time.perf_counter(),
                )
            self.repository.fail_run(run_id, error_code=type(error).__name__, error_message=message)
            self.repository.add_event(
                run_id=run_id,
                event_type="run.error",
                node_name="agent",
                payload={"summary": "本轮工作流未完成。", "error_code": type(error).__name__},
            )

    def get_run(self, anonymous_client_id: str, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_run(anonymous_client_id, run_id)

    def events(self, anonymous_client_id: str, run_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        return self.repository.list_events(anonymous_client_id, run_id, after_id=after_id)

    def cancel_run(self, anonymous_client_id: str, run_id: str) -> bool:
        return self.repository.cancel_run(anonymous_client_id, run_id)

    def rename_conversation(self, anonymous_client_id: str, conversation_id: str, title: str) -> dict[str, Any] | None:
        return self.repository.rename_conversation(anonymous_client_id, conversation_id, title)

    def delete_conversation(self, anonymous_client_id: str, conversation_id: str) -> bool:
        return self.repository.delete_conversation(anonymous_client_id, conversation_id)

    @staticmethod
    def new_anonymous_client_id() -> str:
        return str(uuid4())

    @staticmethod
    def sse_event(event: dict[str, Any]) -> str:
        import json

        payload = json.dumps(event, ensure_ascii=False, default=str)
        return f"id: {event['id']}\nevent: {event['event_type']}\ndata: {payload}\n\n"

    @staticmethod
    def heartbeat() -> str:
        return "event: heartbeat\ndata: {\"status\":\"alive\"}\n\n"

    def stream_events(self, anonymous_client_id: str, run_id: str, *, after_id: int = 0) -> Iterator[str]:
        """Yield a replayable SSE stream until a terminal run status is reached."""
        import time

        last_id = max(0, after_id)
        last_heartbeat = time.monotonic()
        while True:
            events = self.events(anonymous_client_id, run_id, after_id=last_id)
            for event in events:
                last_id = int(event["id"])
                yield self.sse_event(event)
            run = self.get_run(anonymous_client_id, run_id)
            if run is None or run.get("status") in {"completed", "failed", "cancelled"}:
                return
            now = time.monotonic()
            if now - last_heartbeat >= settings.agent_event_heartbeat_seconds:
                yield self.heartbeat()
                last_heartbeat = now
            time.sleep(settings.agent_event_poll_interval_seconds)



