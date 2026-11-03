from __future__ import annotations

import pytest

from app.services.ingestion_job_service import IngestionJobError, IngestionJobService


def test_documents_route_to_mineru_vlm() -> None:
    route, reason = IngestionJobService._resolve_processing_route(
        extension="pptx", source_size_bytes=1024, processing_profile="auto"
    )
    assert route == "mineru_vlm"
    assert "vlm" in reason


def test_ordinary_design_image_routes_to_vision() -> None:
    route, reason = IngestionJobService._resolve_processing_route(
        extension="jpg", source_size_bytes=1024, processing_profile="auto"
    )
    assert route == "vision"
    assert "GPT vision" in reason


def test_text_heavy_image_routes_to_mineru_then_vision() -> None:
    route, reason = IngestionJobService._resolve_processing_route(
        extension="png", source_size_bytes=1024, processing_profile="ocr_and_vision"
    )
    assert route == "mineru_vlm_then_vision"
    assert "OCR" in reason


def test_oversize_text_heavy_image_bypasses_mineru() -> None:
    route, reason = IngestionJobService._resolve_processing_route(
        extension="jpg", source_size_bytes=209_715_201, processing_profile="ocr_and_vision"
    )
    assert route == "vision"
    assert "size limit" in reason


def test_document_rejects_image_only_profile() -> None:
    with pytest.raises(IngestionJobError, match="Documents accept"):
        IngestionJobService._resolve_processing_route(
            extension="pdf", source_size_bytes=1024, processing_profile="vision"
        )



def test_oversize_document_is_rejected_before_mineru_submission() -> None:
    with pytest.raises(IngestionJobError, match="locally normalized PDF page-range derivative"):
        IngestionJobService._require_mineru_size_limit(209_715_201)


def test_mineru_submission_accepts_file_at_precision_limit() -> None:
    IngestionJobService._require_mineru_size_limit(209_715_200)
