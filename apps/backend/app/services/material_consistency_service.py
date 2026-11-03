"""Durable effect-render/material consistency orchestration for the local Admin workspace."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.providers.llm.base import AnswerProvider, AnswerProviderError, MaterialConsistencyJudgement
from app.repositories.material_consistency_repository import MaterialConsistencyRepository
from app.services.visual_analysis_service import VisualAnalysisService
from app.services.vision_image_preprocessor import VisionImagePreprocessError, VisionImagePreprocessor


VERDICTS = {"consistent", "suspected_inconsistent", "unable_to_confirm", "missing_materials"}
MATERIAL_CATEGORIES = {
    "石材": ("石材", "大理石", "花岗岩", "岩板", "洞石", "石"),
    "木饰面": ("木饰面", "木皮", "木材", "木", "木纹"),
    "金属": ("金属", "不锈钢", "铜", "铝", "铁", "古铜"),
    "玻璃": ("玻璃", "镜", "水晶"),
    "涂料": ("乳胶漆", "涂料", "肌理漆", "油漆", "艺术漆"),
    "墙布": ("墙布", "墙纸", "壁布"),
    "皮革": ("皮革", "真皮", "皮质", "皮"),
    "织物": ("布艺", "织物", "绒", "布料"),
    "瓷砖": ("瓷砖", "砖", "瓷"),
}
RESTRICTED_SPECIFICATION = re.compile(r"品牌|型号|基层|阻燃|填充|工程量|用量|规格|厚度|等级|环保", re.IGNORECASE)
CODE_PATTERN = re.compile(r"\b[A-Za-z]{1,8}[-_ ]?\d{1,6}\b")


class MaterialConsistencyError(RuntimeError):
    pass


class MaterialConsistencyService:
    """Compares structured visual facts with same-project document evidence conservatively."""

    def __init__(self, repository: MaterialConsistencyRepository, visual_analysis: VisualAnalysisService,
                 answer_provider: AnswerProvider) -> None:
        self.repository = repository
        self.visual_analysis = visual_analysis
        self.answer_provider = answer_provider
        self.image_preprocessor = VisionImagePreprocessor(
            settings.vision_analysis_derivative_root,
            max_dimension=settings.vision_analysis_max_dimension,
        )

    def get_sources(self, project_id: str) -> dict[str, object]:
        try:
            return self.repository.list_sources(project_id)
        except ValueError as error:
            raise MaterialConsistencyError(str(error)) from error

    def create_audit(self, *, project_id: str, source_specs: list[dict[str, object]], material_document_ids: list[str],
                     analyze_missing_images: bool, explicit_external_export_approved: bool) -> dict[str, object]:
        if not source_specs:
            raise MaterialConsistencyError("至少选择一张效果图或提取的设计图片")
        if not explicit_external_export_approved:
            raise MaterialConsistencyError("需要勾选外部 AI 授权后才能执行核对")
        if material_document_ids and not self.answer_provider.is_configured():
            raise MaterialConsistencyError("LLM gateway is not configured. Configure LLM_API_KEY before running material consistency audit.")
        selected: list[dict[str, object]] = []
        missing_ingestion_ids: list[str] = []
        missing_visual_sources: list[str] = []
        for spec in source_specs:
            source_type = str(spec.get("source_type") or "")
            source_id = str(spec.get("source_id") or "")
            source = self.repository.get_source(project_id=project_id, source_type=source_type, source_id=source_id)
            if not source:
                raise MaterialConsistencyError("所选效果图不属于当前项目，或已不存在")
            if not bool(source.get("can_analyze")):
                raise MaterialConsistencyError(f"图片“{source.get('file_name') or source_id}”没有可用的本地衍生文件，无法核对")
            selected.append({"source_type": source_type, "source_id": source_id})
            if source.get("visual_analysis_status") != "succeeded":
                missing_visual_sources.append(source_id)
                if source_type == "ingestion_job":
                    missing_ingestion_ids.append(source_id)
        # Validate selected evidence documents are local material candidates for this project.
        available_docs = {str(item["document_id"]) for item in self.repository.list_sources(project_id).get("material_documents", [])}
        requested_docs = list(dict.fromkeys(material_document_ids))
        if any(item not in available_docs for item in requested_docs):
            raise MaterialConsistencyError("所选物料资料不属于当前项目，或没有可用的原始文档依据")
        if missing_ingestion_ids and not analyze_missing_images:
            raise MaterialConsistencyError("存在未完成视觉分析的效果图；请勾选“补齐视觉分析”并完成外部 AI 授权")
        if missing_ingestion_ids:
            # The VisualAnalysisService already preserves queue/retry state and only accepts explicitly approved work.
            for offset in range(0, len(missing_ingestion_ids), 20):
                self.visual_analysis.queue_batch(missing_ingestion_ids[offset:offset + 20])
        status = "waiting_for_visual" if missing_ingestion_ids else "queued"
        return self.repository.create_audit(
            project_id=project_id,
            status=status,
            selected_sources=selected,
            material_document_ids=requested_docs,
            approval_record={
                "explicit_external_export_approved": True,
                "included": ["EXIF-free selected images when visual analysis is missing", "structured visual results", "selected material evidence snippets"],
                "excluded": ["original archives", "archive paths", "unselected project documents", "credentials"],
            },
            provider_name=getattr(self.answer_provider, "provider_name", None),
            model_id=settings.llm_model_id,
            prompt_version=settings.material_consistency_prompt_version,
            progress={
                "stage": "waiting_for_visual" if missing_ingestion_ids else "queued",
                "message": "正在等待视觉分析" if missing_ingestion_ids else "已加入核对队列",
                "total_sources": len(selected), "completed_sources": 0,
            },
        )

    def run_next_queued(self) -> dict[str, object] | None:
        self.repository.promote_waiting_audits()
        audit = self.repository.claim_next_audit()
        if not audit:
            return None
        audit_id = str(audit["id"])
        try:
            sources = self.repository.get_sources_for_audit(audit_id)
            evidence = self.repository.list_material_evidence(str(audit["project_id"]), list(audit.get("selected_material_document_ids") or []))
            items: list[dict[str, object]] = []
            source_errors = 0
            for index, source in enumerate(sources, start=1):
                if self.repository.cancellation_requested(audit_id):
                    self.repository.replace_items(audit_id, items)
                    self.repository.finish_audit(audit_id=audit_id, status="cancelled", summary=self._summary(items, total_sources=len(sources)))
                    return self.repository.get_audit(audit_id)
                self.repository.update_progress(audit_id, {
                    "stage": "auditing", "message": f"正在核对 {source.get('file_name') or '图片'}",
                    "total_sources": len(sources), "completed_sources": index - 1,
                })
                try:
                    analysis = self._analysis_for_source(source)
                    source_items = self._items_for_source(source, analysis, evidence)
                    items.extend(source_items)
                except Exception as error:
                    source_errors += 1
                    items.append(self._missing_source_item(source, f"效果图分析不可用：{type(error).__name__}"))
            self.repository.replace_items(audit_id, items)
            if self.repository.cancellation_requested(audit_id):
                state = "cancelled"
            elif source_errors:
                state = "partially_succeeded"
            else:
                state = "succeeded"
            self.repository.finish_audit(audit_id=audit_id, status=state, summary=self._summary(items, total_sources=len(sources)))
        except Exception as error:
            self.repository.finish_audit(audit_id=audit_id, status="failed", summary={}, error_code=type(error).__name__, error_message=str(error)[:4000])
        return self.repository.get_audit(audit_id)

    def _analysis_for_source(self, source: dict[str, object]) -> dict[str, object]:
        source_type = source.get("source_type")
        if source_type == "ingestion_job":
            analysis = source.get("visual_analysis")
            if source.get("visual_analysis_status") == "succeeded" and isinstance(analysis, dict):
                return deepcopy(analysis)
            return {}
        # Extracted PDF/PPT page assets are local derivatives. Reuse a persisted structured result before
        # any external call, so an already analyzed design page is never resent.
        cached = source.get("visual_analysis")
        if source.get("visual_analysis_status") == "succeeded" and isinstance(cached, dict):
            return deepcopy(cached)
        # They may be sent only under the audit's explicit approval; no original archive or path is persisted.
        local_path = Path(str(source.get("local_path") or ""))
        if not local_path.is_file():
            raise MaterialConsistencyError("提取设计页的本地衍生图片缺失")
        derivative = self.image_preprocessor.prepare(local_path, source_sha256=self._source_hash(source, local_path))
        result = self.visual_analysis.provider.analyze_image(
            derivative.derivative_path, source_sha256=derivative.source_sha256,
            prompt_version=settings.llm_vision_prompt_version,
        )
        self.repository.save_image_asset_visual_analysis(
            source_id=str(source["source_id"]), analysis=result.analysis,
            provider_name=getattr(self.visual_analysis.provider, "provider_name", None),
            model_id=settings.llm_model_id, prompt_version=settings.llm_vision_prompt_version,
        )
        return result.analysis

    @staticmethod
    def _source_hash(source: dict[str, object], path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _items_for_source(self, source: dict[str, object], analysis: dict[str, object], evidence: list[dict[str, object]]) -> list[dict[str, object]]:
        records: list[tuple[str, dict[str, object]]] = []
        for field, kind in (("surfaces", "surface"), ("furniture_materials", "furniture")):
            values = analysis.get(field, []) if isinstance(analysis, dict) else []
            if isinstance(values, list):
                records.extend((kind, value) for value in values if isinstance(value, dict))
        if not records:
            return [self._missing_source_item(source, "没有可用于材料核对的结构化构件识别结果")]
        visible_codes = self._visible_codes(analysis)
        return [self._judge_item(source, kind, record, visible_codes, evidence) for kind, record in records]

    def _judge_item(self, source: dict[str, object], kind: str, record: dict[str, object], visible_codes: set[str],
                    evidence: list[dict[str, object]]) -> dict[str, object]:
        terms = self._terms(record) | visible_codes
        candidates = self._rank_candidates(terms, evidence)
        base = self._base_item(source, kind, record)
        if not evidence:
            return {**base, "verdict": "missing_materials", "confidence": 0.99,
                    "reason": "未选择或未找到有效的物料书原始文档依据。", "material_requirement": {}, "evidence": []}
        if not candidates:
            return {**base, "verdict": "missing_materials", "confidence": 0.82,
                    "reason": "物料书中没有找到与该构件、可见材质或材料编号相关的可追溯依据。", "material_requirement": {}, "evidence": []}
        candidate = candidates[0]
        evidence_item = self._evidence_item(candidate, candidate["matched_terms"])
        requirement = self._requirement(candidate)
        visible_category = self._category(self._visual_description(record))
        required_category = self._category(str(candidate.get("content") or "") + " " + json.dumps(candidate.get("metadata") or {}, ensure_ascii=False))
        visual_codes = self._codes(self._visual_description(record)) | visible_codes
        evidence_codes = self._codes(str(candidate.get("content") or "") + " " + json.dumps(candidate.get("metadata") or {}, ensure_ascii=False))
        exact_code = bool(visual_codes & evidence_codes)
        # Exact visible code + same-project material document is the only direct automatic consistency path.
        if exact_code:
            return {**base, "material_requirement": requirement, "verdict": "consistent", "confidence": 0.97,
                    "reason": "效果图可见材料编号与同项目物料书依据中的编号精确对应。", "evidence": [evidence_item]}
        if visible_category and required_category and visible_category != required_category and self._record_confidence(record) >= 0.65:
            return {**base, "material_requirement": requirement, "verdict": "suspected_inconsistent", "confidence": 0.82,
                    "reason": f"视觉识别为“{visible_category}”，物料书候选明确要求“{required_category}”，材质类别存在冲突，需人工复核。", "evidence": [evidence_item]}
        if RESTRICTED_SPECIFICATION.search(str(candidate.get("content") or "")):
            return {**base, "material_requirement": requirement, "verdict": "unable_to_confirm", "confidence": 0.76,
                    "reason": "物料书包含品牌、型号、基层、规格或工程属性；仅凭效果图外观无法确认这些不可见要求。", "evidence": [evidence_item]}
        ai = self._ai_judgement(record, candidate, visible_category, required_category)
        if ai:
            verdict = ai.verdict if ai.verdict in VERDICTS else "unable_to_confirm"
            # An AI may never upgrade a generic appearance match to directly confirmable exact product specs.
            if verdict == "consistent" and (not visible_category or not required_category):
                verdict = "unable_to_confirm"
            return {**base, "material_requirement": requirement, "verdict": verdict, "confidence": ai.confidence,
                    "reason": ai.reason, "evidence": [evidence_item], "ai_judgement": ai.safe_dict()}
        return {**base, "material_requirement": requirement, "verdict": "unable_to_confirm", "confidence": 0.62,
                "reason": "视觉外观与物料书候选存在相关性，但没有可见材料编号或可验证规格，不能据此确认一致。", "evidence": [evidence_item]}

    def _ai_judgement(self, record: dict[str, object], candidate: dict[str, object], visible_category: str | None,
                      required_category: str | None) -> MaterialConsistencyJudgement | None:
        compare = getattr(self.answer_provider, "compare_material_consistency", None)
        if not callable(compare):
            return None
        try:
            return compare(
                visual_material={key: record.get(key) for key in ("element", "material_category", "generic_material", "color", "texture", "finish", "confidence", "limitations")},
                material_evidence={
                    "file_name": candidate.get("file_name"), "page_start": candidate.get("page_start"),
                    "section_path": candidate.get("section_path"), "content": str(candidate.get("content") or "")[:1800],
                    "matched_terms": candidate.get("matched_terms"), "visible_category": visible_category, "required_category": required_category,
                },
                prompt_version=settings.material_consistency_prompt_version,
            )
        except (AnswerProviderError, Exception):
            return None

    def _rank_candidates(self, terms: set[str], evidence: list[dict[str, object]]) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for candidate in evidence:
            text = (str(candidate.get("content") or "") + " " + json.dumps(candidate.get("metadata") or {}, ensure_ascii=False)).casefold().replace(" ", "")
            matched = {term for term in terms if term and term in text}
            if not matched:
                continue
            code_match = bool(self._codes(" ".join(matched)))
            score = 0.35 + 0.13 * len(matched) + (0.48 if code_match else 0.0)
            if candidate.get("chunk_type") == "material_item":
                score += 0.1
            copy = dict(candidate)
            copy["matched_terms"] = sorted(matched)
            copy["score"] = min(0.99, round(score, 3))
            candidates.append(copy)
        candidates.sort(key=lambda item: (float(item["score"]), str(item.get("chunk_type") == "material_item")), reverse=True)
        return candidates[:3]

    @staticmethod
    def _base_item(source: dict[str, object], kind: str, record: dict[str, object]) -> dict[str, object]:
        return {
            "source_type": str(source["source_type"]), "source_id": str(source["source_id"]),
            "ingestion_job_id": str(source["ingestion_job_id"]) if source.get("ingestion_job_id") else None,
            "document_id": str(source["document_id"]) if source.get("document_id") else None,
            "source_file_name": str(source.get("file_name") or "效果图"), "page_number": source.get("page_number"),
            "component_kind": kind, "component_name": str(record.get("element") or "未命名构件"),
            "component_region": record.get("bbox") if isinstance(record.get("bbox"), dict) else {},
            "visual_material": deepcopy(record), "material_requirement": {}, "evidence": [], "ai_judgement": {},
        }

    def _missing_source_item(self, source: dict[str, object], reason: str) -> dict[str, object]:
        return {**self._base_item(source, "source", {"element": "图片整体"}), "verdict": "missing_materials", "confidence": 0.98,
                "reason": reason}

    @staticmethod
    def _visual_description(record: dict[str, object]) -> str:
        return " ".join(str(record.get(key) or "") for key in ("element", "material_category", "generic_material", "visible_material", "color", "texture", "finish", "candidate_keywords"))

    @staticmethod
    def _codes(value: str) -> set[str]:
        return {item.casefold().replace(" ", "") for item in CODE_PATTERN.findall(value or "")}

    def _visible_codes(self, analysis: dict[str, object]) -> set[str]:
        values = analysis.get("visible_text", []) if isinstance(analysis, dict) else []
        return {code for item in values if isinstance(item, dict) for code in self._codes(str(item.get("text") or ""))}

    def _terms(self, record: dict[str, object]) -> set[str]:
        text = self._visual_description(record)
        values = {item.casefold().replace(" ", "") for item in re.findall(r"[A-Za-z]{1,8}[-_ ]?\d{1,6}|[一-鿿]{2,10}", text)}
        return {item for item in values if len(item) >= 2}

    @staticmethod
    def _record_confidence(record: dict[str, object]) -> float:
        value = record.get("confidence")
        return float(value) if isinstance(value, (int, float)) and 0 <= float(value) <= 1 else 0.5

    @staticmethod
    def _category(value: str) -> str | None:
        lower = value.casefold()
        for category, aliases in MATERIAL_CATEGORIES.items():
            if any(alias.casefold() in lower for alias in aliases):
                return category
        return None

    @staticmethod
    def _evidence_item(candidate: dict[str, object], matched_terms: list[str]) -> dict[str, object]:
        return {
            "document_id": str(candidate.get("document_id") or ""), "file_name": candidate.get("file_name"),
            "page_start": candidate.get("page_start"), "page_end": candidate.get("page_end"),
            "section_path": candidate.get("section_path"), "chunk_id": str(candidate.get("chunk_id") or ""),
            "excerpt": str(candidate.get("content") or "")[:1000], "matched_terms": matched_terms,
        }

    @staticmethod
    def _requirement(candidate: dict[str, object]) -> dict[str, object]:
        return {"summary": str(candidate.get("content") or "")[:1200], "chunk_type": candidate.get("chunk_type"),
                "metadata": candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}}

    @staticmethod
    def _summary(items: list[dict[str, object]], *, total_sources: int) -> dict[str, object]:
        counts = Counter(str(item.get("verdict")) for item in items)
        return {
            "total_sources": total_sources, "total_components": len(items),
            "consistent": counts["consistent"], "suspected_inconsistent": counts["suspected_inconsistent"],
            "unable_to_confirm": counts["unable_to_confirm"], "missing_materials": counts["missing_materials"],
        }
