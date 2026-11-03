from app.services.image_asset_policy import classify_image_asset


def test_small_labelled_material_swatch_is_never_decorative() -> None:
    decision = classify_image_asset(adjacent_text="浅色木饰面 M-01，规格 1200×2400mm", width=80, height=80, page_area_ratio=0.001, has_ocr_text=True)
    assert decision.classification == "material_swatch"
    assert decision.chunk_type == "material_swatch"
    assert decision.should_index is True
    assert decision.needs_vision_approval is True


def test_small_drawing_legend_remains_retrievable() -> None:
    decision = classify_image_asset(adjacent_text="天花平面图 图例：L-01 灯具定位", width=90, height=90, page_area_ratio=0.002, has_ocr_text=True)
    assert decision.classification == "drawing_legend"
    assert decision.chunk_type == "image_structure"
    assert decision.should_index is True


def test_decorative_requires_every_exception_condition() -> None:
    decorative = classify_image_asset(width=159, height=159, page_area_ratio=0.004, is_header_footer=True)
    not_decorative = classify_image_asset(width=160, height=159, page_area_ratio=0.004, is_header_footer=True)
    assert decorative.classification == "decorative"
    assert decorative.should_index is False
    assert not_decorative.classification == "unknown"
    assert not_decorative.review_status == "needs_review"
