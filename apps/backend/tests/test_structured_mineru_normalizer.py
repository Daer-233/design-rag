import json
from pathlib import Path
from app.services.mineru_result_normalizer import MinerUResultNormalizer


def test_structured_result_preserves_pages_table_and_material_image_asset(tmp_path: Path) -> None:
    result = tmp_path / "content_list.json"
    result.write_text(json.dumps({"content_list": [
        {"type": "text", "page_idx": 0, "text": "材料说明", "bbox": [0, 0, 100, 20]},
        {"type": "image", "page_idx": 0, "text": "M-01 浅色木饰面 1200×2400mm", "bbox": [10, 20, 90, 100], "page_width": 1000, "page_height": 1000, "img_path": "images/m01.png"},
        {"type": "table", "page_idx": 0, "table_html": "<table><tr><td>品牌</td><td>示例</td></tr></table>", "bbox": [0, 110, 500, 300], "page_width": 1000, "page_height": 1000, "img_path": "images/table.png"},
    ]}, ensure_ascii=False), encoding="utf-8")
    normalized = MinerUResultNormalizer().normalize_result(result)
    assert normalized.parser_structure == "mineru_structured_json"
    parent = normalized.chunks[0]
    assert parent["page_start"] == 1 and parent["page_end"] == 1
    children = parent["children"]
    assert {child["chunk_type"] for child in children} >= {"document_text", "material_swatch", "table", "image_table"}
    assert all(child["page_start"] == 1 and child["page_end"] == 1 for child in children)
    assert len(normalized.image_assets) == 2
    assert normalized.image_assets[0]["image_classification"] == "material_swatch"
    assert normalized.image_assets[0]["page_number"] == 1


def test_structured_material_table_creates_document_confirmed_material_item(tmp_path: Path) -> None:
    result = tmp_path / "material-table.json"
    result.write_text(json.dumps({"content_list": [
        {"type": "text", "page_idx": 3, "text": "B2F 主材表", "bbox": [0, 0, 100, 20]},
        {
            "type": "table",
            "page_idx": 3,
            "table_html": (
                "<table><tr><th>空间</th><th>部位</th><th>材料编号</th><th>材料名称</th>"
                "<th>品牌</th><th>规格</th></tr><tr><td>B2F会客厅</td><td>电视背景墙</td>"
                "<td>MT-03</td><td>深灰天然石材</td><td>示例</td><td>20mm</td></tr></table>"
            ),
            "bbox": [0, 30, 900, 500],
            "page_width": 1000,
            "page_height": 1000,
            "img_path": "images/material-table.png",
        },
    ]}, ensure_ascii=False), encoding="utf-8")
    normalized = MinerUResultNormalizer().normalize_result(result)
    material_items = [
        child
        for parent in normalized.chunks
        for child in parent["children"]
        if child["chunk_type"] == "material_item"
    ]
    assert len(material_items) == 1
    material = material_items[0]
    assert material["page_start"] == 4 and material["page_end"] == 4
    assert material["metadata"]["evidence_type"] == "document_confirmed"
    assert material["metadata"]["element_name"] == "电视背景墙"
    assert material["metadata"]["material_code"] == "MT-03"
    assert material["metadata"]["material_name"] == "深灰天然石材"