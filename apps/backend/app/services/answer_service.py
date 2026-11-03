"""Evidence-constrained answer orchestration.

The answer provider receives only a question plus the already-authorized retrieved
text snippets. PostgreSQL remains authoritative; this service does not send files,
file paths, credentials, or unselected corpus content to the LLM gateway.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.providers.llm.base import AnswerProvider, GroundedAnswer
from app.services.rag_service import SearchService


class AnswerService:
    def __init__(self, search_service: SearchService, answer_provider: AnswerProvider) -> None:
        self.search_service = search_service
        self.answer_provider = answer_provider

    def answer(
        self,
        *,
        query: str,
        project_id: str,
        principal_raw: object,
        evidence_top_k: int | None,
    ) -> dict[str, Any]:
        retrieval = self.search_service.search(
            query=query,
            project_id=project_id,
            principal_raw=principal_raw,
            evidence_top_k=evidence_top_k,
        )
        evidence = retrieval["results"]
        if not evidence:
            return {
                "query": retrieval["query"],
                "project_id": project_id,
                "answer": "当前已授权资料中没有检索到足以回答该问题的证据，暂时无法确认。",
                "insufficient_evidence": True,
                "limitations": ["未检索到可访问的证据，因此没有调用外部回答模型。"],
                "citations": [],
                "pipeline": {**retrieval["pipeline"], "generation": {"called": False, "reason": "no_evidence"}},
                "provider": None,
            }
        packet, source_map, truncation = self._evidence_packet(evidence)
        generated = self.answer_provider.answer_with_evidence(
            question=query,
            evidence_packet=packet,
            allowed_citation_ids=list(source_map),
            prompt_version=settings.answer_prompt_version,
        )
        return self._response(retrieval, generated, source_map, truncation)

    @staticmethod
    def _clip(value: object, limit: int) -> tuple[str, bool]:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text, False
        return text[:limit].rstrip() + "\n[内容因回答上下文长度限制而截断]", True

    @classmethod
    def _evidence_packet(
        cls, evidence: list[dict[str, Any]]
    ) -> tuple[str, dict[str, dict[str, Any]], dict[str, object]]:
        sections: list[str] = []
        source_map: dict[str, dict[str, Any]] = {}
        total = 0
        truncated_ids: list[str] = []
        omitted_ids: list[str] = []
        for index, item in enumerate(evidence, start=1):
            source_id = f"S{index}"
            child, child_truncated = cls._clip(item.get("content"), settings.answer_evidence_child_max_chars)
            parent = item.get("parent_context") or {}
            parent_text, parent_truncated = cls._clip(parent.get("content"), settings.answer_evidence_parent_max_chars)
            citation = item.get("citation") or {}
            header = (
                f"[{source_id}] 文件：{citation.get('file_name') or '未知文件'}；"
                f"类型：{citation.get('file_type') or 'unknown'}；"
                f"页码：{citation.get('page_start') or '-'}-{citation.get('page_end') or '-'}；"
                f"章节：{citation.get('section_path') or '-'}"
            )
            section = f"{header}\n子块证据：\n{child}\n"
            if parent_text:
                section += f"父级上下文：\n{parent_text}\n"
            if total + len(section) > settings.answer_evidence_total_max_chars:
                remaining = settings.answer_evidence_total_max_chars - total
                if remaining < 300:
                    omitted_ids.append(source_id)
                    continue
                section = section[:remaining].rstrip() + "\n[整体证据包已截断]\n"
                child_truncated = True
            sections.append(section)
            total += len(section)
            source_map[source_id] = {
                "citation": citation,
                "chunk_id": item.get("chunk_id"),
                "parent_chunk_id": parent.get("chunk_id"),
                "scores": item.get("scores"),
            }
            if child_truncated or parent_truncated:
                truncated_ids.append(source_id)
        return "\n---\n".join(sections), source_map, {
            "evidence_count_sent": len(source_map),
            "evidence_packet_chars": total,
            "truncated_source_ids": truncated_ids,
            "omitted_source_ids": omitted_ids,
        }

    def _response(
        self,
        retrieval: dict[str, Any],
        generated: GroundedAnswer,
        source_map: dict[str, dict[str, Any]],
        truncation: dict[str, object],
    ) -> dict[str, Any]:
        citations = [
            {"source_id": source_id, **source_map[source_id]}
            for source_id in generated.citations
            if source_id in source_map
        ]
        return {
            "query": retrieval["query"],
            "project_id": retrieval["project_id"],
            "answer": generated.answer,
            "insufficient_evidence": generated.insufficient_evidence,
            "limitations": generated.limitations,
            "citations": citations,
            "pipeline": {
                **retrieval["pipeline"],
                "generation": {
                    "called": True,
                    "mode": "evidence_constrained",
                    "prompt_version": settings.answer_prompt_version,
                    "external_submission": {
                        "included": ["user_query", "retrieved_child_text", "retrieved_parent_context", "source_labels"],
                        "excluded": ["original_files", "file_paths", "credentials", "unretrieved_corpus"],
                        **truncation,
                    },
                },
            },
            "provider": {
                "name": self.answer_provider.provider_name,
                "model_id": settings.llm_model_id,
                "response": generated.provider_response,
            },
        }

