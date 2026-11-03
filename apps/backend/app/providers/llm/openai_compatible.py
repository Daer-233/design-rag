"""OpenAI-compatible vision provider for explicitly authorized image analysis."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.providers.llm.base import VisionAnalysis, VisionProviderError


class OpenAICompatibleVisionProvider:
    provider_name = "nowcoding_openai_compatible"
    _json_fence = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.effective_llm_api_key
        self.model_id = model_id or settings.llm_model_id
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        self.transport = transport

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model_id and self.base_url)

    def analyze_image(
        self,
        source_path: Path,
        *,
        source_sha256: str,
        prompt_version: str,
    ) -> VisionAnalysis:
        if not self.is_configured():
            raise VisionProviderError("LLM gateway is not configured. Set LLM_API_KEY in .env.")
        if not source_path.is_file():
            raise VisionProviderError("Archived source image is missing")
        mime_type = self._image_mime_type(source_path)
        image_url = self._data_url(source_path, mime_type)
        payload = {
            "model": self.model_id,
            "reasoning_effort": settings.llm_reasoning_effort,
            "temperature": 0.1,
            "max_tokens": 2400,
            "messages": [
                {"role": "system", "content": self._system_prompt(prompt_version)},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "分析这张室内设计/建筑设计图片。源文件 SHA-256："
                                f"{source_sha256}。只返回要求的 JSON 对象。"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                    ],
                },
            ],
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                raw = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise VisionProviderError(f"LLM gateway request failed: {type(error).__name__}") from error
        content = self._content_from_response(raw)
        analysis = self._parse_analysis(content)
        return VisionAnalysis(analysis=analysis, provider_response=self._safe_response(raw))

    @classmethod
    def _image_mime_type(cls, source_path: Path) -> str:
        extension = source_path.suffix.lower()
        allowed = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        if extension not in allowed:
            guessed, _ = mimetypes.guess_type(source_path.name)
            raise VisionProviderError(f"Unsupported vision file type '{extension or guessed or 'unknown'}'")
        return allowed[extension]

    @staticmethod
    def _data_url(source_path: Path, mime_type: str) -> str:
        encoded = base64.b64encode(source_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _system_prompt(prompt_version: str) -> str:
        return f"""You are a careful multimodal analyst for interior-design knowledge retrieval.
Prompt version: {prompt_version}.
Analyze both visible text and visual appearance, but never claim that an image alone proves an engineering specification.
Write Chinese values when possible. Return JSON only with this top-level shape:
{{
  "summary": "string",
  "style": {{"primary": "string or null", "secondary": ["string"], "confidence": 0.0}},
  "space_types": ["string"],
  "visible_text": [{{"text": "string", "bbox": [0.0, 0.0, 1.0, 1.0], "confidence": 0.0}}],
  "color_palette": [{{"color": "string", "role": "string", "confidence": 0.0}}],
  "surfaces": [{{
    "element": "电视背景墙/墙面/地面/天花/柜体等",
    "material_category": "石材/岩板/木饰面/金属/玻璃/涂料/未知",
    "generic_material": "仅描述画面可见的通用材料，不填写无法看见的品牌型号",
    "color": "string or null",
    "texture": "string or null",
    "finish": "string or null",
    "visible_area_ratio": 0.0,
    "bbox": [0.0, 0.0, 1.0, 1.0],
    "confidence": 0.0,
    "evidence_basis": "可见纹理、拼缝、反光、文字标注等",
    "candidate_keywords": ["材料编号或检索关键词"],
    "evidence_status": "vision_inferred or unknown",
    "limitations": ["string"]
  }}],
  "furniture_materials": [{{
    "element": "沙发/茶几/座椅/柜体等",
    "component": "外层面料/台面/框架等",
    "visible_material": "画面可见材质；无法判断时写未知",
    "color": "string or null",
    "texture": "string or null",
    "bbox": [0.0, 0.0, 1.0, 1.0],
    "confidence": 0.0,
    "evidence_basis": "string",
    "candidate_keywords": ["string"],
    "evidence_status": "vision_inferred or unknown",
    "limitations": ["例如：无法从图片确认内部填充、基层和品牌型号"]
  }}],
  "materials": [{{"name": "legacy-compatible material", "location": "string", "confidence": 0.0}}],
  "furniture_and_decor": [{{"name": "legacy-compatible item", "description": "string", "confidence": 0.0}}],
  "lighting": {{"description": "string", "confidence": 0.0}},
  "layout": {{"description": "string", "confidence": 0.0}},
  "visual_features": ["string"],
  "search_text": "include space, element, generic material, visible text/material codes and uncertainty",
  "limitations": ["string"]
}}
Bounding boxes and visible_area_ratio use normalized 0..1 values. visible_area_ratio means only the approximate visible image-area share, not BOQ quantity or construction material ratio.
Use evidence_status=vision_inferred for a visible inference and unknown when the image is insufficient. Never output document_confirmed or candidate_match; the local knowledge-base matcher adds candidate matches later.
Do not infer concealed substrate, structural layers, exact product, brand, model, fire rating, or actual engineering quantity from appearance alone.
Use numbers between 0 and 1 for confidence. Empty arrays or null are allowed when uncertain."""

    @classmethod
    def _content_from_response(cls, raw: object) -> str:
        if not isinstance(raw, dict):
            raise VisionProviderError("LLM gateway returned an invalid JSON response")
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise VisionProviderError("LLM gateway response did not include a completion choice")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise VisionProviderError("LLM gateway response did not include a message")
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "".join(str(text) for text in texts)
        raise VisionProviderError("LLM gateway response message did not include text content")

    @classmethod
    def _parse_analysis(cls, content: str) -> dict[str, object]:
        cleaned = cls._json_fence.sub("", content.strip()).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise VisionProviderError("LLM gateway did not return a valid JSON object") from error
        if not isinstance(value, dict):
            raise VisionProviderError("LLM gateway result must be a JSON object")
        summary = value.get("summary")
        search_text = value.get("search_text")
        if not isinstance(summary, str) or not summary.strip():
            raise VisionProviderError("LLM gateway result is missing a non-empty summary")
        if not isinstance(search_text, str) or not search_text.strip():
            value["search_text"] = summary
        return value

    @staticmethod
    def _safe_response(raw: dict[str, Any]) -> dict[str, object]:
        choices = raw.get("choices")
        finish_reason = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = choices[0].get("finish_reason")
        return {
            "id": raw.get("id"),
            "model": raw.get("model"),
            "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else {},
            "finish_reason": finish_reason,
        }
