"""Explicitly authorized image-analysis workflow using the configured LLM gateway."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.providers.llm.base import VisionProvider, VisionProviderError
from app.repositories.rag_repository import RagRepository
from app.services.rag_service import IngestionService
from app.services.vision_image_preprocessor import VisionImagePreprocessError, VisionImagePreprocessor


class VisualAnalysisJobError(RuntimeError):
    pass


class VisualAnalysisService:
    """Create local jobs first; only ``submit`` sends image bytes to the LLM gateway."""

    def __init__(
        self,
        repository: RagRepository,
        provider: VisionProvider,
        ingestion_service: IngestionService,
        image_preprocessor: VisionImagePreprocessor | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.ingestion_service = ingestion_service
        self.image_preprocessor = image_preprocessor or VisionImagePreprocessor(
            settings.vision_analysis_derivative_root,
            max_dimension=settings.vision_analysis_max_dimension,
        )

    def create(self, ingestion_job_id: str) -> dict[str, object]:
        try:
            return self.repository.create_visual_analysis_job(
                ingestion_job_id=ingestion_job_id,
                provider_name=self.provider.provider_name,
                model_id=settings.llm_model_id,
                prompt_version=settings.llm_vision_prompt_version,
                provider_config={"base_url": settings.llm_base_url, "transport": "openai_compatible"},
                trace_id=str(uuid4()),
            )
        except ValueError as error:
            raise VisualAnalysisJobError(str(error)) from error

    def create_batch(self, ingestion_job_ids: list[str]) -> list[dict[str, object]]:
        """Create local draft jobs only; this does not send image bytes externally."""
        self._validate_batch_size(ingestion_job_ids)
        results: list[dict[str, object]] = []
        for ingestion_job_id in ingestion_job_ids:
            try:
                results.append({"id": ingestion_job_id, "outcome": "created", "job": self.create(ingestion_job_id)})
            except VisualAnalysisJobError as error:
                results.append({"id": ingestion_job_id, "outcome": "failed", "reason": str(error)})
        return results

    def queue_info(self) -> dict[str, object]:
        """Safe UI metadata; credentials and provider configuration stay server-side."""
        return {
            "model_id": settings.llm_model_id,
            "max_images": settings.llm_vision_batch_max_images,
            "configured": self.provider.is_configured(),
        }

    def queue_batch(self, ingestion_job_ids: list[str]) -> list[dict[str, object]]:
        """Durably queue explicitly approved images and return without external calls."""
        self._validate_batch_size(ingestion_job_ids)
        if not self.provider.is_configured():
            raise VisualAnalysisJobError("LLM gateway is not configured. Configure the local gateway before queueing image analysis.")
        results: list[dict[str, object]] = []
        for ingestion_job_id in ingestion_job_ids:
            try:
                queued = self.repository.queue_visual_analysis_for_ingestion(
                    ingestion_job_id=ingestion_job_id,
                    provider_name=self.provider.provider_name,
                    model_id=settings.llm_model_id,
                    prompt_version=settings.llm_vision_prompt_version,
                    provider_config={"base_url": settings.llm_base_url, "transport": "openai_compatible"},
                    trace_id=str(uuid4()),
                )
                results.append({"id": ingestion_job_id, **queued})
            except ValueError as error:
                results.append({"id": ingestion_job_id, "outcome": "failed", "reason": str(error)})
        return results

    def get(self, job_id: str) -> dict[str, object]:
        result = self.repository.get_visual_analysis_job(job_id)
        if not result:
            raise VisualAnalysisJobError("Visual analysis job not found")
        return result

    def submit(self, job_id: str) -> dict[str, object]:
        """Backward-compatible submit endpoint: queue an existing local job only."""
        try:
            return self.repository.queue_visual_analysis_job(job_id)
        except ValueError as error:
            raise VisualAnalysisJobError(str(error)) from error

    def submit_batch(self, job_ids: list[str]) -> list[dict[str, object]]:
        self._validate_batch_size(job_ids)
        results: list[dict[str, object]] = []
        for job_id in job_ids:
            try:
                results.append({"id": job_id, **self.submit(job_id)})
            except VisualAnalysisJobError as error:
                results.append({"id": job_id, "outcome": "failed", "reason": str(error)})
        return results

    def cancel(self, job_id: str) -> dict[str, object]:
        try:
            return self.repository.request_visual_analysis_cancel(job_id)
        except ValueError as error:
            raise VisualAnalysisJobError(str(error)) from error

    def cancel_batch(self, job_ids: list[str]) -> list[dict[str, object]]:
        self._validate_batch_size(job_ids)
        results: list[dict[str, object]] = []
        for job_id in job_ids:
            try:
                results.append(self.cancel(job_id))
            except VisualAnalysisJobError as error:
                results.append({"id": job_id, "outcome": "failed", "reason": str(error)})
        return results

    def run_queued_job(self, job_id: str) -> dict[str, object] | None:
        """Execute one claimed local job. Called only by the full local backend worker."""
        if not self.repository.claim_visual_analysis_job(job_id):
            return None
        job = self.get(job_id)
        if not self.provider.is_configured():
            return self.repository.record_visual_analysis_failure_or_retry(
                job_id,
                error_code="LLM_NOT_CONFIGURED",
                error_message="LLM gateway is not configured for this queued task.",
            )
        try:
            derivative = self.image_preprocessor.prepare(
                Path(str(job["source_file_path"])),
                source_sha256=str(job["source_sha256"]),
            )
            derivative_asset = self.repository.ensure_visual_analysis_derivative_asset(
                ingestion_job_id=str(job["ingestion_job_id"]),
                source_sha256=derivative.source_sha256,
                derivative_path=str(derivative.derivative_path),
                derivative_sha256=derivative.derivative_sha256,
                content_type=derivative.content_type,
                size_bytes=derivative.size_bytes,
                derivation_type=derivative.derivation_type,
                generator_version=derivative.generator_version,
                metadata=derivative.request_metadata(),
            )
        except (VisionImagePreprocessError, OSError, ValueError) as error:
            return self.repository.record_visual_analysis_failure_or_retry(
                job_id, error_code=type(error).__name__, error_message=str(error)
            )
        provider_request = {
            "source_sha256": str(job["source_sha256"]),
            "source_file_name": str(job["source_file_name"]),
            "model_id": str(job["model_id"]),
            "prompt_version": str(job["prompt_version"]),
            "image_transport": "base64_data_url",
            "analysis_derivative": {
                **derivative.request_metadata(),
                "document_asset_id": str(derivative_asset["id"]),
            },
        }
        try:
            # The provider receives only the EXIF-free local derivative, never the archived original.
            result = self.provider.analyze_image(
                derivative.derivative_path,
                source_sha256=str(job["source_sha256"]),
                prompt_version=str(job["prompt_version"]),
            )
        except (VisionProviderError, OSError) as error:
            return self.repository.record_visual_analysis_failure_or_retry(
                job_id, error_code=type(error).__name__, error_message=str(error)
            )
        except Exception as error:
            return self.repository.record_visual_analysis_failure_or_retry(
                job_id,
                error_code=type(error).__name__,
                error_message=f"Visual analysis request failed: {type(error).__name__}",
            )

        # A cancellation received while the external model was running wins at this
        # boundary: the paid external call may finish, but no result is ingested.
        if not self.repository.prepare_visual_analysis_materialization(job_id):
            self.repository.record_visual_analysis_cancelled(
                job_id, provider_request=provider_request, provider_response=result.provider_response
            )
            return {"outcome": "cancelled", "job": self.get(job_id)}
        analysis = self._attach_material_candidates(job, result.analysis)
        try:
            ingestion = self.ingestion_service.ingest(self._derived_ingestion_payload(job, analysis))
        except Exception as error:
            return self.repository.record_visual_analysis_failure_or_retry(
                job_id,
                error_code=type(error).__name__,
                error_message=f"Visual analysis materialization failed: {type(error).__name__}",
            )
        self.repository.record_visual_analysis_success(
            job_id,
            provider_request=provider_request,
            provider_response=result.provider_response,
            analysis=analysis,
            derived_document_version_id=str(ingestion["document_version_id"]),
        )
        return {"outcome": "succeeded", "job": self.get(job_id), "ingestion": ingestion}

    @staticmethod
    def _validate_batch_size(job_ids: list[str]) -> None:
        if not job_ids:
            raise VisualAnalysisJobError("At least one visual analysis job is required")
        if len(job_ids) > settings.llm_vision_batch_max_images:
            raise VisualAnalysisJobError(
                f"A visual-analysis round is limited to {settings.llm_vision_batch_max_images} images"
            )
        if len(set(job_ids)) != len(job_ids):
            raise VisualAnalysisJobError("Visual analysis job IDs must be unique in one batch")

    @classmethod
    def _derived_ingestion_payload(cls, job: dict[str, object], analysis: dict[str, object]) -> dict[str, object]:
        chunks = cls._analysis_to_chunks(job, analysis)
        document_metadata = job.get("document_metadata")
        metadata = dict(document_metadata) if isinstance(document_metadata, dict) else {}
        metadata["latest_visual_analysis"] = {
            "model_id": str(job["model_id"]),
            "prompt_version": str(job["prompt_version"]),
            "source_sha256": str(job["source_sha256"]),
            "summary": str(analysis["summary"]),
        }
        scope = job.get("document_access_scope")
        return {
            "project": {
                "external_key": job["project_external_key"],
                "name": job["project_name"],
                "metadata": {},
            },
            "document": {
                "external_key": job["document_external_key"],
                "source_uri": job["source_uri"],
                "file_name": job["file_name"],
                "file_type": job["file_type"],
                "metadata": metadata,
                "access_scope": scope if isinstance(scope, dict) else {},
            },
            "parser": {
                "provider": str(job["provider_name"]),
                "model": str(job["model_id"]),
                "version": str(job["prompt_version"]),
                "parsed_content_uri": None,
                "parsed_payload": {
                    "visual_analysis_job_id": str(job["id"]),
                    "source_document_version_id": (
                        str(job["source_document_version_id"]) if job.get("source_document_version_id") else None
                    ),
                    "source_sha256": str(job["source_sha256"]),
                    "response_storage": "postgresql.visual_analysis_jobs.analysis",
                },
                "source_confidence": cls._confidence(analysis.get("style"), default=0.65),
            },
            "chunks": chunks,
        }

    @classmethod
    def _analysis_to_chunks(cls, job: dict[str, object], analysis: dict[str, object]) -> list[dict[str, object]]:
        summary = cls._text(analysis.get("summary"))
        search_text = cls._text(analysis.get("search_text")) or summary
        document_metadata = job.get("document_metadata")
        document_metadata = document_metadata if isinstance(document_metadata, dict) else {}
        raw_page = document_metadata.get("source_page_number")
        page_number = int(raw_page) if isinstance(raw_page, int) or (isinstance(raw_page, str) and raw_page.isdigit()) else None
        metadata = {
            "content_origin": "vision_generated",
            "evidence_type": "vision_inferred",
            "review_status": "pending",
            "visual_analysis_job_id": str(job["id"]),
            "model_id": str(job["model_id"]),
            "prompt_version": str(job["prompt_version"]),
            "source_sha256": str(job["source_sha256"]),
            "source_document_name": document_metadata.get("source_document_name"),
            "source_page_number": page_number,
        }

        def chunk(
            *, title: str, value: object, chunk_type: str, confidence: float,
            bbox: object = None, evidence_type: str = "vision_inferred",
        ) -> dict[str, object]:
            local_metadata = {**metadata, "evidence_type": evidence_type}
            locator: dict[str, object] = {"kind": "image_visual_analysis"}
            if page_number is not None:
                locator["page"] = page_number
            if isinstance(bbox, list) and len(bbox) == 4:
                locator["bbox"] = bbox
            result: dict[str, object] = {
                "ordinal": 0,
                "content": f"{title}\n{cls._json_text(value)}",
                "chunk_type": chunk_type,
                "metadata": local_metadata,
                "source_locator": locator,
                "review_status": "pending",
                "source_confidence": confidence,
            }
            if page_number is not None:
                result["page_start"] = page_number
                result["page_end"] = page_number
                result["section_path"] = f"第 {page_number} 页 / {title}"
            return result

        children: list[dict[str, object]] = []
        general_sources = [
            ("风格与概述", {"summary": summary, "style": analysis.get("style"), "space_types": analysis.get("space_types", [])}),
            ("空间、布局与光线", {"layout": analysis.get("layout"), "lighting": analysis.get("lighting"), "visual_features": analysis.get("visual_features", [])}),
            ("色彩与旧版材料摘要", {"color_palette": analysis.get("color_palette", []), "materials": analysis.get("materials", [])}),
            ("家具与装饰摘要", {"furniture_and_decor": analysis.get("furniture_and_decor", []), "limitations": analysis.get("limitations", [])}),
        ]
        for title, value in general_sources:
            children.append(chunk(title=title, value=value, chunk_type="vision_analysis", confidence=cls._confidence(analysis.get("style"), default=0.65)))

        for record in analysis.get("visible_text", []) if isinstance(analysis.get("visible_text"), list) else []:
            if not isinstance(record, dict) or not cls._text(record.get("text")):
                continue
            children.append(chunk(
                title="图片可见文字", value=record, chunk_type="image_ocr",
                confidence=cls._confidence(record, default=0.6), bbox=record.get("bbox"),
            ))

        for record in analysis.get("surfaces", []) if isinstance(analysis.get("surfaces"), list) else []:
            if not isinstance(record, dict):
                continue
            evidence_status = str(record.get("evidence_status") or "vision_inferred")
            evidence_type = "unknown" if evidence_status == "unknown" else "vision_inferred"
            children.append(chunk(
                title=f"界面材料：{cls._text(record.get('element')) or '未命名构件'}",
                value={**record, "visible_area_ratio_note": "仅为当前画面可见面积估算，不是工程材料配比或BOQ数量"},
                chunk_type="vision_surface", confidence=cls._confidence(record, default=0.55),
                bbox=record.get("bbox"), evidence_type=evidence_type,
            ))

        for record in analysis.get("furniture_materials", []) if isinstance(analysis.get("furniture_materials"), list) else []:
            if not isinstance(record, dict):
                continue
            evidence_status = str(record.get("evidence_status") or "vision_inferred")
            evidence_type = "unknown" if evidence_status == "unknown" else "vision_inferred"
            children.append(chunk(
                title=f"家具材质：{cls._text(record.get('element')) or '未命名家具'}",
                value=record, chunk_type="furniture_material",
                confidence=cls._confidence(record, default=0.55), bbox=record.get("bbox"),
                evidence_type=evidence_type,
            ))

        children.append(chunk(title="检索描述", value={"search_text": search_text}, chunk_type="vision_analysis", confidence=0.7))
        for ordinal, child in enumerate(children):
            child["ordinal"] = ordinal
        parent = chunk(
            title="图片视觉分析概述", value={"summary": summary, "search_text": search_text},
            chunk_type="vision_analysis", confidence=cls._confidence(analysis.get("style"), default=0.65),
        )
        parent["ordinal"] = 0
        parent["children"] = children
        return [parent]

    def _attach_material_candidates(
        self, job: dict[str, object], analysis: dict[str, object]
    ) -> dict[str, object]:
        """Attach same-project document candidates without upgrading them to confirmed evidence."""
        enriched = deepcopy(analysis)
        loader = getattr(self.repository, "list_project_material_evidence", None)
        project_id = job.get("project_id")
        if not callable(loader) or not project_id:
            return enriched
        try:
            candidates = loader(str(project_id), limit=1000)
        except Exception:
            return enriched
        visible_codes = {
            token.casefold()
            for record in enriched.get("visible_text", []) if isinstance(record, dict)
            for token in re.findall(r"[A-Za-z]{1,6}[-_ ]?\d{1,5}", str(record.get("text") or ""))
        }
        for field in ("surfaces", "furniture_materials"):
            records = enriched.get(field)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                terms = self._material_terms(record) | visible_codes
                matches: list[dict[str, object]] = []
                for candidate in candidates:
                    score, matched = self._material_candidate_score(terms, candidate)
                    if score < 0.45:
                        continue
                    matches.append({
                        "evidence_type": "candidate_match",
                        "match_score": round(score, 3),
                        "matched_terms": sorted(matched),
                        "source_id": f"chunk:{candidate.get('chunk_id')}",
                        "chunk_id": str(candidate.get("chunk_id") or ""),
                        "document_id": str(candidate.get("document_id") or ""),
                        "file_name": candidate.get("file_name"),
                        "file_type": candidate.get("file_type"),
                        "page_start": candidate.get("page_start"),
                        "page_end": candidate.get("page_end"),
                        "section_path": candidate.get("section_path"),
                        "material_metadata": candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {},
                        "evidence_excerpt": str(candidate.get("content") or "")[:500],
                        "confirmation_note": "同项目知识库候选匹配；需由材料表编号、图纸标注或人工复核确认。",
                    })
                matches.sort(key=lambda item: float(item["match_score"]), reverse=True)
                record["candidate_matches"] = matches[:3]
        return enriched

    @staticmethod
    def _material_terms(record: dict[str, object]) -> set[str]:
        values: list[str] = []
        for key in ("element", "material_category", "generic_material", "visible_material", "color", "texture", "finish"):
            value = record.get(key)
            if isinstance(value, str):
                values.append(value)
        keywords = record.get("candidate_keywords")
        if isinstance(keywords, list):
            values.extend(str(value) for value in keywords if value)
        terms: set[str] = set()
        for value in values:
            terms.update(token.casefold() for token in re.findall(r"[A-Za-z]{1,6}[-_ ]?\d{1,5}|[一-鿿]{2,8}", value))
        return {term.replace(" ", "") for term in terms if len(term.replace(" ", "")) >= 2}

    @staticmethod
    def _material_candidate_score(
        terms: set[str], candidate: dict[str, object]
    ) -> tuple[float, set[str]]:
        if not terms:
            return 0.0, set()
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        haystack = (str(candidate.get("content") or "") + " " + json.dumps(metadata, ensure_ascii=False)).casefold().replace(" ", "")
        matched = {term for term in terms if term in haystack}
        if not matched:
            return 0.0, set()
        code_match = any(re.fullmatch(r"[a-z]{1,6}[-_]?\d{1,5}", term) for term in matched)
        score = min(0.85, 0.35 + 0.15 * len(matched))
        if code_match:
            score = max(score, 0.95)
        if candidate.get("chunk_type") == "material_item":
            score = min(0.98, score + 0.08)
        return score, matched

    @staticmethod
    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _json_text(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _confidence(value: object, *, default: float) -> float:
        if isinstance(value, dict):
            confidence = value.get("confidence")
            if isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1:
                return float(confidence)
        return default
