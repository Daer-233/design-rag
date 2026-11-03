"""Deterministic, explainable coarse ranking between hybrid recall and cross-encoder rerank."""

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class CoarseRankedCandidate:
    item: dict[str, Any]
    score: float
    breakdown: dict[str, float]


def _normalise(value: float, maximum: float) -> float:
    return value / maximum if maximum > 0 else 0.0


def _metadata_match_score(query: str, item: dict[str, Any]) -> float:
    metadata = item.get("chunk_metadata") or {}
    values = [item.get("section_path") or "", json.dumps(metadata, ensure_ascii=False)]
    haystack = " ".join(str(value).lower() for value in values)
    terms = {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)}
    if not terms:
        return 0.0
    return min(1.0, sum(term in haystack for term in terms) / len(terms))


def _review_score(status: object) -> float:
    return {"approved": 1.0, "reviewed": 0.8, "pending": 0.45, "rejected": 0.0}.get(
        str(status).lower(), 0.3
    )


def _source_quality_score(metadata: object) -> float:
    if isinstance(metadata, dict):
        value = metadata.get("source_quality_score", 0.5)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            pass
    return 0.5


def _evidence_type_score(item: dict[str, Any]) -> float:
    """Prefer source-grounded facts before visual descriptions in coarse recall."""
    metadata = item.get("chunk_metadata") or {}
    origin = str(metadata.get("content_origin", "")).lower() if isinstance(metadata, dict) else ""
    chunk_type = str(item.get("chunk_type", "")).lower()
    factual_types = {
        "material_item", "material_swatch", "table", "image_table", "image_ocr", "document_text",
    }
    semantic_types = {"image_caption", "image_structure", "vision_analysis"}
    if chunk_type in factual_types or origin in {"document_text", "ocr_extracted", "table_extracted"}:
        return 1.0
    if chunk_type in semantic_types or origin == "vision_generated":
        return 0.35
    return 0.6


def rank_candidates(query: str, items: list[dict[str, Any]], limit: int) -> list[CoarseRankedCandidate]:
    max_fusion = max((float(item["fusion_score"]) for item in items), default=0.0)
    ranked: list[CoarseRankedCandidate] = []
    for item in items:
        fusion = _normalise(float(item["fusion_score"]), max_fusion)
        metadata_match = _metadata_match_score(query, item)
        review = _review_score(item.get("review_status"))
        confidence = max(0.0, min(1.0, float(item.get("source_confidence", 0.0))))
        quality = _source_quality_score(item.get("chunk_metadata"))
        evidence_type = _evidence_type_score(item)
        score = (
            0.52 * fusion
            + 0.14 * metadata_match
            + 0.09 * review
            + 0.09 * confidence
            + 0.04 * quality
            + 0.12 * evidence_type
        )
        ranked.append(
            CoarseRankedCandidate(
                item=item,
                score=score,
                breakdown={
                    "fusion_normalized": fusion,
                    "metadata_match": metadata_match,
                    "review_status": review,
                    "source_confidence": confidence,
                    "source_quality": quality,
                    "evidence_type": evidence_type,
                },
            )
        )
    return sorted(ranked, key=lambda candidate: candidate.score, reverse=True)[:limit]
