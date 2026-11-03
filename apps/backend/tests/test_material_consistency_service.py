from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.llm.base import VisionAnalysis
from app.services.material_consistency_service import MaterialConsistencyError, MaterialConsistencyService


class _NoAi:
    def compare_material_consistency(self, **_kwargs):
        return None


def _service() -> MaterialConsistencyService:
    service = object.__new__(MaterialConsistencyService)
    service.answer_provider = _NoAi()
    return service


def _source() -> dict[str, object]:
    return {
        "source_type": "ingestion_job",
        "source_id": "00000000-0000-0000-0000-000000000001",
        "ingestion_job_id": "00000000-0000-0000-0000-000000000001",
        "document_id": "00000000-0000-0000-0000-000000000010",
        "file_name": "会所效果图.png",
        "page_number": 3,
    }


def _evidence(content: str, *, document_id: str = "00000000-0000-0000-0000-000000000020") -> dict[str, object]:
    return {
        "chunk_id": "00000000-0000-0000-0000-000000000021",
        "document_id": document_id,
        "file_name": "物料书.pdf",
        "page_start": 8,
        "page_end": 8,
        "section_path": "大堂 / 电视背景墙",
        "chunk_type": "material_item",
        "metadata": {"content_origin": "document_text"},
        "content": content,
    }


def test_exact_visible_material_code_is_consistent() -> None:
    service = _service()
    record = {"element": "电视背景墙", "material_category": "石材", "generic_material": "深灰石材", "confidence": 0.91}
    item = service._judge_item(_source(), "surface", record, {"mt-03"}, [_evidence("电视背景墙材料编号 MT-03，深灰石材")])

    assert item["verdict"] == "consistent"
    evidence = item["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["document_id"] == "00000000-0000-0000-0000-000000000020"
    assert evidence[0]["page_start"] == 8
    assert evidence[0]["chunk_id"] == "00000000-0000-0000-0000-000000000021"
    assert "mt-03" in evidence[0]["matched_terms"]


def test_explicit_category_conflict_is_suspected_inconsistent() -> None:
    service = _service()
    record = {"element": "电视背景墙", "material_category": "石材", "generic_material": "天然石材", "confidence": 0.9}
    item = service._judge_item(_source(), "surface", record, set(), [_evidence("电视背景墙采用浅色木饰面，木皮哑光处理")])

    assert item["verdict"] == "suspected_inconsistent"
    assert item["evidence"][0]["document_id"] == "00000000-0000-0000-0000-000000000020"


def test_product_model_is_unable_to_confirm_from_appearance() -> None:
    service = _service()
    record = {"element": "沙发", "material_category": "皮革", "generic_material": "浅色皮革", "confidence": 0.85}
    item = service._judge_item(_source(), "furniture", record, set(), [_evidence("沙发采用进口皮革，品牌 XXX，型号 S-102，基层阻燃等级 B1")])

    assert item["verdict"] == "unable_to_confirm"
    assert "仅凭效果图外观无法确认" in item["reason"]


def test_missing_document_evidence_is_not_inferred() -> None:
    service = _service()
    record = {"element": "地面", "material_category": "石材", "generic_material": "米色石材", "confidence": 0.8}

    item = service._judge_item(_source(), "surface", record, set(), [])

    assert item["verdict"] == "missing_materials"
    assert item["evidence"] == []


def test_extracted_asset_reuses_cached_vision_analysis_without_external_call() -> None:
    service = _service()
    cached = {"surfaces": [{"element": "电视背景墙", "material_category": "石材"}]}
    service.visual_analysis = SimpleNamespace(provider=SimpleNamespace(analyze_image=lambda *_args, **_kwargs: pytest.fail("should not call AI")))

    analysis = service._analysis_for_source({"source_type": "image_asset", "visual_analysis_status": "succeeded", "visual_analysis": cached})

    assert analysis == cached
    assert analysis is not cached


def test_extracted_asset_persists_first_successful_analysis() -> None:
    service = _service()
    source_file = Path.cwd() / "tmp" / "test-material-consistency-page.png"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"test-image")
    saved: dict[str, object] = {}

    class _Repository:
        def save_image_asset_visual_analysis(self, **kwargs):
            saved.update(kwargs)

    class _Preprocessor:
        def prepare(self, path: Path, *, source_sha256: str):
            assert path == source_file
            return SimpleNamespace(derivative_path=source_file, source_sha256=source_sha256)

    service.repository = _Repository()
    service.image_preprocessor = _Preprocessor()
    service.visual_analysis = SimpleNamespace(provider=SimpleNamespace(
        provider_name="fake-vision",
        analyze_image=lambda *_args, **_kwargs: VisionAnalysis(analysis={"surfaces": []}, provider_response={}),
    ))

    analysis = service._analysis_for_source({
        "source_type": "image_asset", "source_id": "00000000-0000-0000-0000-000000000030",
        "local_path": str(source_file), "visual_analysis_status": None,
    })

    assert analysis == {"surfaces": []}
    assert saved["source_id"] == "00000000-0000-0000-0000-000000000030"
    assert saved["analysis"] == {"surfaces": []}
    assert saved["provider_name"] == "fake-vision"


def test_create_audit_requires_explicit_external_authorization() -> None:
    service = _service()
    with pytest.raises(MaterialConsistencyError, match="外部 AI 授权"):
        service.create_audit(
            project_id="00000000-0000-0000-0000-000000000040",
            source_specs=[{"source_type": "ingestion_job", "source_id": "00000000-0000-0000-0000-000000000041"}],
            material_document_ids=["00000000-0000-0000-0000-000000000042"],
            analyze_missing_images=True,
            explicit_external_export_approved=False,
        )
