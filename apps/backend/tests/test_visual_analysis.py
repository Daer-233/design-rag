from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.providers.llm.base import VisionProviderError
from app.providers.llm.openai_compatible import OpenAICompatibleVisionProvider
from app.services.visual_analysis_service import VisualAnalysisJobError, VisualAnalysisService
from app.services.vision_image_preprocessor import VisionImageDerivative, VisionImagePreprocessor


def test_openai_compatible_vision_provider_sends_image_only_in_explicit_call(tmp_path: Path) -> None:
    source = tmp_path / "case.jpg"
    source.write_bytes(b"fake-jpeg-bytes")
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        assert request.method == "POST"
        assert request.url == "https://gateway.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.6-terra"
        parts = payload["messages"][1]["content"]
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "gpt-5.6-terra",
                "usage": {"total_tokens": 123},
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "summary": "现代简约客厅，使用浅色木饰面。",
                        "style": {"primary": "现代简约", "secondary": [], "confidence": 0.87},
                        "search_text": "现代简约 浅色木饰面 客厅",
                    }, ensure_ascii=False)},
                }],
            },
        )

    provider = OpenAICompatibleVisionProvider(
        base_url="https://gateway.example/v1",
        api_key="test-key",
        model_id="gpt-5.6-terra",
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze_image(source, source_sha256="a" * 64, prompt_version="design-image-v1")

    assert result.analysis["summary"] == "现代简约客厅，使用浅色木饰面。"
    assert result.provider_response == {
        "id": "chatcmpl-test",
        "model": "gpt-5.6-terra",
        "usage": {"total_tokens": 123},
        "finish_reason": "stop",
    }
    assert len(recorded) == 1


def test_openai_compatible_vision_provider_rejects_non_json_completion(tmp_path: Path) -> None:
    source = tmp_path / "case.png"
    source.write_bytes(b"fake-png-bytes")
    provider = OpenAICompatibleVisionProvider(
        base_url="https://gateway.example/v1",
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        ),
    )
    with pytest.raises(VisionProviderError, match="valid JSON"):
        provider.analyze_image(source, source_sha256="a" * 64, prompt_version="design-image-v1")


def test_visual_analysis_chunks_and_batch_limit() -> None:
    job = {
        "id": "11111111-1111-1111-1111-111111111111",
        "model_id": "gpt-5.6-terra",
        "prompt_version": "design-image-v1",
        "source_sha256": "a" * 64,
    }
    analysis = {
        "summary": "折衷装饰风格的会客空间。",
        "search_text": "折衷装饰 黄铜 深色木材 会客空间",
        "style": {"primary": "折衷装饰", "confidence": 0.8},
        "materials": [{"name": "木材", "confidence": 0.8}],
    }
    chunks = VisualAnalysisService._analysis_to_chunks(job, analysis)
    assert len(chunks) == 1
    assert len(chunks[0]["children"]) == 5
    assert chunks[0]["children"][0]["chunk_type"] == "vision_analysis"
    with pytest.raises(VisualAnalysisJobError, match="limited to 20"):
        VisualAnalysisService._validate_batch_size([str(index) for index in range(21)])


def test_visual_analysis_material_chunks_keep_page_and_evidence_levels() -> None:
    job = {
        "id": "11111111-1111-1111-1111-111111111111",
        "model_id": "gpt-5.6-terra",
        "prompt_version": "design-image-v2-materials",
        "source_sha256": "a" * 64,
        "document_metadata": {
            "source_document_name": "华润顶层会所.pdf",
            "source_page_number": 7,
        },
    }
    analysis = {
        "summary": "会客厅",
        "search_text": "会客厅 电视背景墙 沙发",
        "visible_text": [{"text": "MT-03", "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 0.9}],
        "surfaces": [{
            "element": "电视背景墙",
            "material_category": "石材",
            "generic_material": "深灰石纹板材",
            "visible_area_ratio": 0.32,
            "bbox": [0.1, 0.2, 0.8, 0.8],
            "confidence": 0.82,
            "evidence_status": "vision_inferred",
        }],
        "furniture_materials": [{
            "element": "沙发",
            "component": "外层面料",
            "visible_material": "米灰色织物",
            "bbox": [0.1, 0.6, 0.7, 0.95],
            "confidence": 0.72,
            "evidence_status": "vision_inferred",
        }],
    }

    children = VisualAnalysisService._analysis_to_chunks(job, analysis)[0]["children"]
    by_type = {child["chunk_type"]: child for child in children}
    assert {"image_ocr", "vision_surface", "furniture_material"} <= set(by_type)
    for chunk_type in ("image_ocr", "vision_surface", "furniture_material"):
        child = by_type[chunk_type]
        assert child["page_start"] == 7 and child["page_end"] == 7
        assert child["source_locator"]["page"] == 7
        assert child["metadata"]["source_document_name"] == "华润顶层会所.pdf"
    assert by_type["vision_surface"]["metadata"]["evidence_type"] == "vision_inferred"
    assert "不是工程材料配比" in by_type["vision_surface"]["content"]


def test_material_candidate_match_never_upgrades_visual_inference_to_document_confirmed() -> None:
    class Repository:
        @staticmethod
        def list_project_material_evidence(project_id: str, *, limit: int) -> list[dict[str, object]]:
            assert project_id == "22222222-2222-2222-2222-222222222222"
            assert limit == 1000
            return [{
                "chunk_id": "33333333-3333-3333-3333-333333333333",
                "document_id": "44444444-4444-4444-4444-444444444444",
                "file_name": "主材表.pdf",
                "file_type": "pdf",
                "page_start": 12,
                "page_end": 12,
                "section_path": "会客厅 / 主材表",
                "chunk_type": "material_item",
                "content": "材料编号：MT-03；部位：电视背景墙；材料名称：深灰天然石材",
                "metadata": {"material_code": "MT-03", "element_name": "电视背景墙"},
            }]

    service = object.__new__(VisualAnalysisService)
    service.repository = Repository()  # type: ignore[attr-defined]
    analysis = {
        "visible_text": [{"text": "MT-03", "confidence": 0.96}],
        "surfaces": [{
            "element": "电视背景墙",
            "generic_material": "深灰石纹板材",
            "evidence_status": "vision_inferred",
        }],
    }
    enriched = service._attach_material_candidates(
        {"project_id": "22222222-2222-2222-2222-222222222222"}, analysis
    )
    surface = enriched["surfaces"][0]
    match = surface["candidate_matches"][0]
    assert surface["evidence_status"] == "vision_inferred"
    assert match["evidence_type"] == "candidate_match"
    assert match["match_score"] >= 0.95
    assert match["source_id"] == "chunk:33333333-3333-3333-3333-333333333333"
    assert "人工复核确认" in match["confirmation_note"]


def test_vision_image_preprocessor_corrects_orientation_removes_exif_and_reuses_derivative(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    image = Image.new("RGB", (1800, 3000), "navy")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, format="JPEG", exif=exif)
    source_sha256 = VisionImagePreprocessor._sha256(source)
    preprocessor = VisionImagePreprocessor(tmp_path / "derived", max_dimension=512)
    first = preprocessor.prepare(source, source_sha256=source_sha256)
    second = preprocessor.prepare(source, source_sha256=source_sha256)
    assert first.orientation_corrected is True
    assert (first.source_width, first.source_height) == (1800, 3000)
    assert max(first.derivative_width, first.derivative_height) == 512
    assert first.derivative_path == second.derivative_path
    assert first.derivative_sha256 == second.derivative_sha256
    with Image.open(first.derivative_path) as derivative:
        assert derivative.getexif() == {}
        assert derivative.size == (512, 307)


class _QueuedRepository:
    def __init__(self, *, materialize: bool = True) -> None:
        self.materialize = materialize
        self.claimed: list[str] = []
        self.cancelled: list[str] = []
        self.retries: list[tuple[str, str, str]] = []
        self.succeeded: list[str] = []
        self.derivative_assets: list[dict[str, object]] = []
        self.job = {
            "id": "11111111-1111-1111-1111-111111111111",
            "ingestion_job_id": "33333333-3333-3333-3333-333333333333",
            "source_sha256": "a" * 64,
            "source_file_name": "case.jpg",
            "source_file_path": "C:/safe/case.jpg",
            "model_id": "gpt-5.6-terra",
            "prompt_version": "design-image-v1",
            "project_external_key": "case",
            "project_name": "案例项目",
            "document_external_key": "case-image",
            "source_uri": "file:///safe/case.jpg",
            "file_name": "case.jpg",
            "file_type": "jpg",
            "document_metadata": {},
            "document_access_scope": {},
        }

    def claim_visual_analysis_job(self, job_id: str) -> bool:
        self.claimed.append(job_id)
        return True

    def get_visual_analysis_job(self, job_id: str) -> dict[str, object]:
        return self.job

    def ensure_visual_analysis_derivative_asset(self, **kwargs: object) -> dict[str, object]:
        self.derivative_assets.append(dict(kwargs))
        return {"id": "44444444-4444-4444-4444-444444444444"}

    def prepare_visual_analysis_materialization(self, job_id: str) -> bool:
        return self.materialize

    def record_visual_analysis_cancelled(self, job_id: str, **_: object) -> None:
        self.cancelled.append(job_id)

    def record_visual_analysis_failure_or_retry(self, job_id: str, *, error_code: str, error_message: str) -> dict[str, object]:
        self.retries.append((job_id, error_code, error_message))
        return {"outcome": "retry_queued", "job": self.job}

    def record_visual_analysis_success(self, job_id: str, **_: object) -> None:
        self.succeeded.append(job_id)


class _QueuedProvider:
    provider_name = "test-provider"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.source_paths: list[Path] = []

    def is_configured(self) -> bool:
        return True

    def analyze_image(self, source_path: Path, *_: object, **__: object):
        self.calls += 1
        self.source_paths.append(source_path)
        if self.error:
            raise self.error
        from app.providers.llm.base import VisionAnalysis
        return VisionAnalysis(
            analysis={"summary": "客厅案例", "search_text": "客厅"},
            provider_response={"id": "provider-request"},
        )


class _QueuedPreprocessor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def prepare(self, source_path: Path, *, source_sha256: str) -> VisionImageDerivative:
        self.calls.append((source_path, source_sha256))
        return VisionImageDerivative(
            source_path=source_path,
            derivative_path=Path("C:/safe/derived-analysis.jpg"),
            source_sha256=source_sha256,
            derivative_sha256="b" * 64,
            source_width=2400,
            source_height=1600,
            derivative_width=2048,
            derivative_height=1365,
            content_type="image/jpeg",
            size_bytes=1234,
            orientation_corrected=True,
            max_dimension=2048,
        )


class _QueuedIngestion:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def ingest(self, payload: dict[str, object]) -> dict[str, str]:
        self.payloads.append(payload)
        return {"document_version_id": "22222222-2222-2222-2222-222222222222"}


def test_queued_visual_job_cancellation_skips_knowledge_base_materialization() -> None:
    repository = _QueuedRepository(materialize=False)
    provider = _QueuedProvider()
    ingestion = _QueuedIngestion()
    preprocessor = _QueuedPreprocessor()
    service = VisualAnalysisService(repository, provider, ingestion, preprocessor)  # type: ignore[arg-type]
    result = service.run_queued_job("11111111-1111-1111-1111-111111111111")
    assert result is not None
    assert result["outcome"] == "cancelled"
    assert provider.calls == 1
    assert provider.source_paths == [Path("C:/safe/derived-analysis.jpg")]
    assert repository.derivative_assets[0]["derivative_sha256"] == "b" * 64
    assert repository.cancelled == ["11111111-1111-1111-1111-111111111111"]
    assert ingestion.payloads == []


def test_queued_visual_job_records_failure_for_single_retry_policy() -> None:
    repository = _QueuedRepository()
    provider = _QueuedProvider(error=VisionProviderError("gateway timeout"))
    ingestion = _QueuedIngestion()
    preprocessor = _QueuedPreprocessor()
    service = VisualAnalysisService(repository, provider, ingestion, preprocessor)  # type: ignore[arg-type]
    result = service.run_queued_job("11111111-1111-1111-1111-111111111111")
    assert result is not None
    assert result["outcome"] == "retry_queued"
    assert repository.retries[0][1] == "VisionProviderError"
    assert ingestion.payloads == []