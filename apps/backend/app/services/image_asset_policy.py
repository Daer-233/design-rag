"""Deterministic PDF/PPTX image-asset classification and processing policy.

The policy never drops an image asset.  It only decides whether the asset is
immediately indexable, needs a later vision approval, or is the narrow decorative
exception that remains metadata-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MATERIAL_KEYWORDS = (
    "材料", "材质", "石材", "木饰面", "木材", "墙布", "墙纸", "肌理漆", "乳胶漆", "涂料",
    "古铜", "金属", "色板", "色彩", "样板", "编号", "型号", "规格", "品牌", "单位", "尺寸",
    "mm", "cm", "㎡", "m²", "m2", "五金", "灯具", "洁具", "家具", "设备",
)
DRAWING_KEYWORDS = (
    "平面", "立面", "剖面", "节点", "大样", "图例", "索引", "天花", "地铺", "标高", "轴线",
    "详图", "构造", "尺寸", "定位", "铺装",
)
TABLE_KEYWORDS = ("表", "清单", "数量", "单位", "规格", "型号", "品牌", "序号")


@dataclass(frozen=True)
class ImageAssetDecision:
    classification: str
    processing_status: str
    chunk_type: str | None
    content_origin: str | None
    should_index: bool
    needs_vision_approval: bool
    review_status: str
    reason: str


def _text(value: object) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def classify_image_asset(
    *,
    adjacent_text: str = "",
    caption_text: str = "",
    block_type: str = "",
    width: int | None = None,
    height: int | None = None,
    page_area_ratio: float | None = None,
    is_header_footer: bool = False,
    highly_duplicate: bool = False,
    has_ocr_text: bool = False,
    table_context: dict[str, Any] | None = None,
) -> ImageAssetDecision:
    """Classify an image with a conservative, evidence-first policy.

    Any uncertain asset is preserved as ``unknown`` and marked ``needs_review``;
    size is never a direct deletion or non-ingestion condition.
    """
    context = " ".join((_text(adjacent_text), _text(caption_text), _text(block_type))).strip()
    lower_type = _text(block_type).lower()
    has_material = _has_any(context, MATERIAL_KEYWORDS)
    has_drawing = _has_any(context, DRAWING_KEYWORDS)
    # A field such as “型号” or “规格” by itself is common on a material label;
    # it does not prove that the region is a table.  Structural table context or a
    # stronger table cue is required.
    has_table = bool(table_context) or "table" in lower_type or _has_any(context, ("表", "清单", "数量", "序号"))
    has_business_label = has_ocr_text or has_material or has_drawing or has_table
    pixels_small = width is not None and height is not None and width < 160 and height < 160
    tiny_area = page_area_ratio is not None and page_area_ratio < 0.005

    if has_table:
        return ImageAssetDecision("table_image", "structured_completed", "image_table", "ocr_extracted", True, False, "pending", "table or table-adjacent image")
    # Drawings take precedence over generic product keywords such as “灯具”.
    if has_drawing:
        drawing_type = "drawing_legend" if "图例" in context else (
            "floor_plan" if "平面" in context else "elevation" if "立面" in context else "section" if "剖面" in context else "detail_drawing"
        )
        return ImageAssetDecision(drawing_type, "ocr_completed" if has_ocr_text else "structured_completed", "image_structure", "ocr_extracted", True, True, "pending", "drawing, legend, or adjacent drawing label")
    if has_material:
        classification = "material_swatch" if _has_any(context, ("样板", "色板", "色彩", "石材", "墙布", "木饰面", "肌理", "古铜")) else "material_board"
        return ImageAssetDecision(classification, "ocr_completed" if has_ocr_text else "structured_completed", "material_swatch", "ocr_extracted", True, True, "pending", "material label or material-board semantic")
    if "furniture" in lower_type or "家具" in context:
        return ImageAssetDecision("furniture_image", "vision_pending_approval", "image_caption", "document_text", True, True, "pending", "furniture product image")
    if "equipment" in lower_type or "设备" in context or "灯具" in context or "洁具" in context:
        return ImageAssetDecision("equipment_image", "vision_pending_approval", "image_caption", "document_text", True, True, "pending", "equipment or product image")
    if "render" in lower_type or "image" in lower_type or context:
        return ImageAssetDecision("rendering", "vision_pending_approval", "image_caption", "document_text", True, True, "pending", "visual image retained for later explicit approval")

    if tiny_area and pixels_small and not has_business_label and (is_header_footer or highly_duplicate):
        return ImageAssetDecision("decorative", "skipped_decorative", None, None, False, False, "approved", "all decorative exception conditions satisfied")
    return ImageAssetDecision("unknown", "needs_review", "image_ocr", "ocr_extracted", True, False, "needs_review", "insufficient semantic evidence; preserve for review")
