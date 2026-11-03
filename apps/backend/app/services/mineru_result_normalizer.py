"""Normalize MinerU artifacts into page-aware parent/child chunks and image assets.

Structured MinerU JSON is preferred because it preserves page, bounding-box, table and
image order. Markdown remains a compatibility fallback only when no usable structured
payload is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
from typing import Any
from zipfile import ZipFile, is_zipfile

from app.services.image_asset_policy import classify_image_asset


@dataclass(frozen=True)
class NormalizedChunk:
    ordinal: int
    content: str
    chunk_type: str
    section_path: str | None
    metadata: dict[str, object]
    children: list[dict[str, object]]


@dataclass(frozen=True)
class NormalizedMinerUResult:
    chunks: list[dict[str, object]]
    image_assets: list[dict[str, object]]
    parser_structure: str


class MinerUResultNormalizer:
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    _image_types = frozenset({"image", "img", "figure", "picture", "table", "chart"})

    def __init__(self, *, parent_max_chars: int = 5_000, child_max_chars: int = 1_000) -> None:
        self.parent_max_chars = parent_max_chars
        self.child_max_chars = child_max_chars

    def normalize_result(self, result_path: Path) -> NormalizedMinerUResult:
        """Prefer structured MinerU JSON and fall back to Markdown without inventing pages."""
        structured = self._load_structured_blocks(result_path)
        if structured:
            chunks, assets = self._structured_to_parent_child_chunks(structured)
            if chunks:
                return NormalizedMinerUResult(chunks, assets, "mineru_structured_json")
        markdown = self.result_to_markdown(result_path)
        return NormalizedMinerUResult(
            self.markdown_to_parent_child_chunks(markdown), [], "markdown_fallback"
        )

    def markdown_to_parent_child_chunks(self, markdown: str) -> list[dict[str, object]]:
        sections = self._sections(markdown)
        chunks: list[dict[str, object]] = []
        for title_path, content in sections:
            for parent_text in self._split_text(content, self.parent_max_chars):
                children = self._child_chunks(parent_text, title_path)
                if not children:
                    continue
                chunks.append(
                    {
                        "ordinal": len(chunks),
                        "content": parent_text,
                        "chunk_type": "section",
                        "metadata": {"parser_structure": "markdown", "content_origin": "document_text"},
                        "section_path": title_path,
                        "source_locator": {},
                        "review_status": "pending",
                        "source_confidence": 0.8,
                        "children": children,
                    }
                )
        return chunks

    @staticmethod
    def result_to_markdown(result_path: Path) -> str:
        if is_zipfile(result_path):
            with ZipFile(result_path) as archive:
                markdown_names = sorted(
                    name for name in archive.namelist() if not name.endswith("/") and name.lower().endswith(".md")
                )
                if not markdown_names:
                    raise ValueError("MinerU archive does not contain a Markdown file")
                return archive.read(markdown_names[0]).decode("utf-8-sig")
        raw = result_path.read_text(encoding="utf-8-sig")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(value, dict):
            for key in ("markdown", "md", "content"):
                if isinstance(value.get(key), str):
                    return str(value[key])
        raise ValueError("MinerU JSON result did not contain Markdown content")

    def _load_structured_blocks(self, result_path: Path) -> list[dict[str, Any]]:
        candidates: list[tuple[str, object]] = []
        if is_zipfile(result_path):
            with ZipFile(result_path) as archive:
                names = sorted(name for name in archive.namelist() if name.lower().endswith(".json"))
                names.sort(key=self._structured_json_priority)
                for name in names:
                    try:
                        candidates.append((name, json.loads(archive.read(name).decode("utf-8-sig"))))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
        else:
            try:
                candidates.append((result_path.name, json.loads(result_path.read_text(encoding="utf-8-sig"))))
            except (OSError, json.JSONDecodeError):
                return []
        for _, value in candidates:
            blocks = self._extract_blocks(value)
            if blocks:
                return blocks
        return []

    @staticmethod
    def _structured_json_priority(name: str) -> tuple[int, str]:
        lower = name.lower()
        if "content_list" in lower:
            return (0, lower)
        if "middle" in lower or "layout" in lower:
            return (1, lower)
        return (2, lower)

    @classmethod
    def _extract_blocks(cls, value: object) -> list[dict[str, Any]]:
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return [dict(item) for item in value]
        if not isinstance(value, dict):
            return []
        for key in ("content_list", "blocks", "content", "layout", "items"):
            nested = value.get(key)
            if isinstance(nested, list) and all(isinstance(item, dict) for item in nested):
                return [dict(item) for item in nested]
        pages = value.get("pages") or value.get("pdf_info")
        if isinstance(pages, list):
            collected: list[dict[str, Any]] = []
            for page_index, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                items = page.get("blocks") or page.get("content") or page.get("layout") or []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        record = dict(item)
                        record.setdefault("page_idx", page.get("page_idx", page_index))
                        collected.append(record)
            return collected
        return []

    def _structured_to_parent_child_chunks(
        self, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        pages: dict[int, list[dict[str, Any]]] = {}
        for index, block in enumerate(blocks):
            page = self._page_number(block, fallback=index)
            pages.setdefault(page, []).append({**block, "_order": index})
        chunks: list[dict[str, object]] = []
        assets: list[dict[str, object]] = []
        for page, page_blocks in sorted(pages.items()):
            text_records = [self._block_text(block) for block in page_blocks]
            page_text = "\n".join(text for text in text_records if text).strip()
            parent_content = page_text or f"第 {page} 页：页面包含待处理图像或版面内容。"
            children: list[dict[str, object]] = []
            for index, block in enumerate(page_blocks):
                block_type = self._block_type(block)
                text = self._block_text(block)
                bbox = self._bbox(block)
                locator = {"page": page, "bbox": bbox, "block_order": int(block["_order"])}
                if block_type == "table":
                    adjacent = self._adjacent_text(text_records, index)
                    asset, image_child = self._image_asset_from_block(
                        block=block, page=page, bbox=bbox, adjacent_text=adjacent,
                        block_type=block_type, ordinal=len(assets),
                    )
                    assets.append(asset)
                    if text:
                        children.append({
                            "ordinal": len(children), "content": text, "chunk_type": "table",
                            "metadata": {"parser_structure": "mineru_content_list", "content_origin": "ocr_extracted", "evidence_type": "document_confirmed", "mineru_block_type": block_type, "image_asset_ref": asset["asset_ref"]},
                            "page_start": page, "page_end": page, "section_path": self._section_text(block),
                            "source_locator": locator, "review_status": "pending", "source_confidence": 0.9,
                        })
                        for material_child in self._material_items_from_table(
                            text=text, adjacent_text=adjacent, page=page, locator=locator,
                            section_path=self._section_text(block),
                        ):
                            material_child["ordinal"] = len(children)
                            children.append(material_child)
                    if image_child:
                        image_child["ordinal"] = len(children)
                        children.append(image_child)
                    continue
                if self._is_image_block(block, block_type):
                    adjacent = self._adjacent_text(text_records, index)
                    asset, child = self._image_asset_from_block(
                        block=block, page=page, bbox=bbox, adjacent_text=adjacent,
                        block_type=block_type, ordinal=len(assets),
                    )
                    assets.append(asset)
                    if child:
                        child["ordinal"] = len(children)
                        children.append(child)
                    continue
                if not text:
                    continue
                child_type = "table" if block_type == "table" else "document_text"
                origin = "ocr_extracted" if block_type in {"ocr", "image_ocr"} else "document_text"
                children.append(
                    {
                        "ordinal": len(children),
                        "content": text,
                        "chunk_type": child_type,
                        "metadata": {
                            "parser_structure": "mineru_content_list",
                            "content_origin": origin,
                            "mineru_block_type": block_type,
                        },
                        "page_start": page,
                        "page_end": page,
                        "section_path": self._section_text(block),
                        "source_locator": locator,
                        "review_status": "pending",
                        "source_confidence": 0.9,
                    }
                )
            chunks.append(
                {
                    "ordinal": len(chunks),
                    "content": parent_content,
                    "chunk_type": "page",
                    "metadata": {"parser_structure": "mineru_content_list", "content_origin": "document_text"},
                    "page_start": page,
                    "page_end": page,
                    "section_path": f"第 {page} 页",
                    "source_locator": {"page": page},
                    "review_status": "pending",
                    "source_confidence": 0.9,
                    "children": children,
                }
            )
        return chunks, assets

    def _image_asset_from_block(
        self,
        *,
        block: dict[str, Any],
        page: int,
        bbox: dict[str, float],
        adjacent_text: str,
        block_type: str,
        ordinal: int,
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        caption = self._block_text(block)
        width, height = self._dimensions(block, bbox)
        decision = classify_image_asset(
            adjacent_text=adjacent_text,
            caption_text=caption,
            block_type=block_type,
            width=width,
            height=height,
            page_area_ratio=self._page_area_ratio(block, bbox),
            has_ocr_text=bool(caption),
            table_context=self._table_context(block),
        )
        image_path = self._first_string(block, "img_path", "image_path", "path", "image", "uri")
        stable_ref = sha256(
            f"{page}|{json.dumps(bbox, sort_keys=True)}|{image_path or ''}|{ordinal}".encode("utf-8")
        ).hexdigest()
        asset = {
            "asset_ref": stable_ref,
            "page_number": page,
            "bbox": bbox,
            "image_hash": self._first_string(block, "image_hash", "sha256", "hash"),
            "archive_member": image_path,
            "image_classification": decision.classification,
            "processing_status": decision.processing_status,
            "adjacent_text": adjacent_text,
            "caption_text": caption,
            "table_context": self._table_context(block),
            "review_status": decision.review_status,
            "metadata": {
                "mineru_block_type": block_type,
                "classification_reason": decision.reason,
                "width": width,
                "height": height,
                "page_area_ratio": self._page_area_ratio(block, bbox),
            },
        }
        if not decision.should_index or not decision.chunk_type:
            return asset, None
        content_parts = [f"图片类型：{decision.classification}"]
        if adjacent_text:
            content_parts.append(f"相邻文字：{adjacent_text}")
        if caption:
            content_parts.append(f"图片文字/图注：{caption}")
        if not adjacent_text and not caption:
            content_parts.append(f"第 {page} 页图片区域，待人工复核。")
        return asset, {
            "ordinal": ordinal,
            "content": "\n".join(content_parts),
            "chunk_type": decision.chunk_type,
            "metadata": {
                "parser_structure": "mineru_content_list",
                "content_origin": decision.content_origin,
                "image_asset_ref": stable_ref,
                "image_classification": decision.classification,
                "vision_required": decision.needs_vision_approval,
            },
            "page_start": page,
            "page_end": page,
            "section_path": f"第 {page} 页图片",
            "source_locator": {"page": page, "bbox": bbox},
            "review_status": decision.review_status,
            "source_confidence": 0.85 if decision.classification != "unknown" else 0.5,
        }

    @staticmethod
    def _page_number(block: dict[str, Any], *, fallback: int) -> int:
        for key in ("page_number", "page_no", "page_idx", "page_id"):
            value = block.get(key)
            try:
                numeric = int(value)
            except (TypeError, ValueError):
                continue
            return numeric + 1 if key == "page_idx" else max(numeric, 1)
        return fallback + 1

    @staticmethod
    def _block_type(block: dict[str, Any]) -> str:
        value = block.get("type") or block.get("block_type") or block.get("category") or "text"
        return str(value).strip().lower()

    @classmethod
    def _is_image_block(cls, block: dict[str, Any], block_type: str) -> bool:
        if block_type in cls._image_types:
            return True
        return any(key in block for key in ("img_path", "image_path", "image", "image_url"))

    @staticmethod
    def _bbox(block: dict[str, Any]) -> dict[str, float]:
        raw = block.get("bbox") or block.get("box") or block.get("position")
        if isinstance(raw, dict):
            values = {key: raw.get(key) for key in ("x0", "y0", "x1", "y1")}
        elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
            values = dict(zip(("x0", "y0", "x1", "y1"), raw[:4], strict=True))
        else:
            values = {}
        parsed: dict[str, float] = {}
        for key, value in values.items():
            try:
                parsed[key] = float(value)
            except (TypeError, ValueError):
                continue
        return parsed

    @staticmethod
    def _dimensions(block: dict[str, Any], bbox: dict[str, float]) -> tuple[int | None, int | None]:
        width = block.get("width") or block.get("w")
        height = block.get("height") or block.get("h")
        try:
            return int(width), int(height)
        except (TypeError, ValueError):
            if {"x0", "x1", "y0", "y1"}.issubset(bbox):
                return int(abs(bbox["x1"] - bbox["x0"])), int(abs(bbox["y1"] - bbox["y0"]))
        return None, None

    @staticmethod
    def _page_area_ratio(block: dict[str, Any], bbox: dict[str, float]) -> float | None:
        direct = block.get("page_area_ratio") or block.get("area_ratio")
        try:
            if direct is not None:
                return float(direct)
        except (TypeError, ValueError):
            pass
        page_width = block.get("page_width")
        page_height = block.get("page_height")
        if not {"x0", "x1", "y0", "y1"}.issubset(bbox):
            return None
        try:
            image_area = abs((bbox["x1"] - bbox["x0"]) * (bbox["y1"] - bbox["y0"]))
            return image_area / (float(page_width) * float(page_height))
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    _material_header_aliases = {
        "space_name": ("空间", "区域", "楼层", "房间"),
        "element_name": ("使用部位", "应用部位", "部位", "构件", "位置"),
        "material_code": ("材料编号", "物料编号", "编号", "编码", "色号"),
        "material_name": ("材料名称", "物料名称", "材料", "材质", "物料", "名称"),
        "brand": ("品牌", "厂家", "供应商"),
        "model": ("型号", "款号"),
        "specification": ("规格", "尺寸", "厚度"),
        "color": ("颜色", "色彩", "色号"),
        "quantity": ("数量", "用量", "面积"),
        "unit": ("单位",),
    }
    _material_context_terms = ("材料", "材质", "物料", "主材", "软装", "家具", "饰面", "面料", "石材", "木饰面")

    @classmethod
    def _material_items_from_table(
        cls, *, text: str, adjacent_text: str, page: int,
        locator: dict[str, object], section_path: str | None,
    ) -> list[dict[str, object]]:
        rows = cls._table_rows(text)
        if len(rows) < 2:
            return []
        headers = [cls._plain_cell(value) for value in rows[0]]
        mapped: list[str | None] = [cls._material_header_key(value) for value in headers]
        recognized = sum(value is not None for value in mapped)
        context = f"{adjacent_text}\n{text}".casefold()
        if recognized < 2 or not any(term in context for term in cls._material_context_terms):
            return []
        results: list[dict[str, object]] = []
        for row_index, raw_row in enumerate(rows[1:], start=1):
            cells = [cls._plain_cell(value) for value in raw_row]
            if not any(cells):
                continue
            record: dict[str, object] = {}
            for index, key in enumerate(mapped):
                if key and index < len(cells) and cells[index]:
                    record[key] = cells[index]
            if not record.get("material_name"):
                fallback = next((cell for cell in cells if cell and not re.fullmatch(r"\d+(?:\.\d+)?", cell)), "")
                if fallback:
                    record["material_name"] = fallback
            if len(record) < 2 and not record.get("material_code"):
                continue
            record.update({
                "evidence_type": "document_confirmed",
                "content_origin": "ocr_extracted",
                "source_table_headers": headers,
                "source_table_row": row_index,
            })
            label = "；".join(
                f"{headers[index] if index < len(headers) else key}：{cells[index]}"
                for index, key in enumerate(mapped)
                if key and index < len(cells) and cells[index]
            )
            results.append({
                "ordinal": 0,
                "content": f"材料条目\n{label or '；'.join(cells)}",
                "chunk_type": "material_item",
                "metadata": {
                    "parser_structure": "mineru_material_table",
                    **record,
                },
                "page_start": page,
                "page_end": page,
                "section_path": section_path or f"第 {page} 页 / 材料表",
                "source_locator": {**locator, "table_row": row_index},
                "review_status": "pending",
                "source_confidence": 0.9,
            })
        return results

    @classmethod
    def _table_rows(cls, text: str) -> list[list[str]]:
        html_rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL)
        if html_rows:
            rows: list[list[str]] = []
            for row in html_rows:
                cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
                if cells:
                    rows.append(cells)
            return rows
        rows = []
        for line in text.splitlines():
            line = line.strip().strip("|")
            if not line:
                continue
            cells = [cell.strip() for cell in re.split(r"\t|\s*\|\s*", line)]
            if len(cells) > 1 and not all(re.fullmatch(r":?-{3,}:?", cell or "-") for cell in cells):
                rows.append(cells)
        return rows

    @staticmethod
    def _plain_cell(value: str) -> str:
        plain = re.sub(r"<[^>]+>", " ", unescape(value))
        return re.sub(r"\s+", " ", plain).strip()

    @classmethod
    def _material_header_key(cls, header: str) -> str | None:
        normalized = re.sub(r"\s+", "", header)
        for key, aliases in cls._material_header_aliases.items():
            if any(alias in normalized for alias in aliases):
                return key
        return None

    @staticmethod
    def _table_context(block: dict[str, Any]) -> dict[str, object]:
        data: dict[str, object] = {}
        for key in ("table_body", "table_html", "html", "latex", "cells"):
            value = block.get(key)
            if value:
                data[key] = value
        return data

    @classmethod
    def _block_text(cls, block: dict[str, Any]) -> str:
        values: list[str] = []
        for key in ("text", "content", "text_content", "caption", "table_body", "table_html", "html"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        return "\n".join(dict.fromkeys(values))

    @staticmethod
    def _section_text(block: dict[str, Any]) -> str | None:
        for key in ("section", "title", "heading"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_string(block: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _adjacent_text(texts: list[str], index: int) -> str:
        nearby = [value for value in texts[max(0, index - 2) : index + 3] if value]
        return "\n".join(nearby)[:2000]

    def _sections(self, markdown: str) -> list[tuple[str | None, str]]:
        levels: list[str] = []
        content: list[str] = []
        result: list[tuple[str | None, str]] = []
        current_path: str | None = None

        def flush() -> None:
            text = "\n".join(content).strip()
            if text:
                result.append((current_path, text))

        for raw_line in markdown.replace("\r\n", "\n").split("\n"):
            match = self.heading_pattern.match(raw_line)
            if match:
                flush()
                content.clear()
                level = len(match.group(1))
                title = match.group(2).strip()
                levels[level - 1 :] = [title]
                current_path = " / ".join(levels)
            else:
                content.append(raw_line)
        flush()
        if not result:
            text = markdown.strip()
            return [(None, text)] if text else []
        return result

    def _child_chunks(self, parent_text: str, section_path: str | None) -> list[dict[str, object]]:
        return [
            {
                "ordinal": ordinal,
                "content": child_text,
                "chunk_type": "document_text",
                "metadata": {"parser_structure": "markdown", "content_origin": "document_text"},
                "section_path": section_path,
                "source_locator": {},
                "review_status": "pending",
                "source_confidence": 0.8,
            }
            for ordinal, child_text in enumerate(self._split_text(parent_text, self.child_max_chars))
        ]

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list[str]:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        result: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.extend(
                    paragraph[start : start + max_chars].strip()
                    for start in range(0, len(paragraph), max_chars)
                    if paragraph[start : start + max_chars].strip()
                )
                continue
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) > max_chars and buffer:
                result.append(buffer)
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            result.append(buffer)
        return result
