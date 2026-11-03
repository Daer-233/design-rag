"""Offline retrieval metrics. Unreviewed labels never produce quality scores."""

from typing import Any


def score_retrieval(case: dict[str, Any], retrieval: dict[str, Any], k: int) -> dict[str, Any]:
    if k < 1:
        raise ValueError("k must be positive")
    evidence = retrieval.get("results", [])[:k]
    chunk_ids = list(dict.fromkeys(str(item["chunk_id"]) for item in evidence))
    asset_ids = set(str(item["asset_id"]) for item in retrieval.get("images", []))
    reviewed = case.get("annotation_status") == "reviewed"
    metrics: dict[str, float | None] = dict.fromkeys([
        "recall_at_k", "evidence_precision_at_k", "reciprocal_rank",
        "citation_source_precision", "image_precision", "image_recall", "empty_result_correctness",
    ])
    gold = case.get("relevant_chunk_ids")
    if reviewed and isinstance(gold, list):
        relevant = set(map(str, gold))
        if relevant:
            hits = len(set(chunk_ids) & relevant)
            metrics["recall_at_k"] = hits / len(relevant)
            metrics["evidence_precision_at_k"] = hits / len(chunk_ids) if chunk_ids else 0.0
            metrics["reciprocal_rank"] = next(
                (1 / rank for rank, cid in enumerate(chunk_ids, 1) if cid in relevant), 0.0
            )
        else:
            metrics["empty_result_correctness"] = float(not evidence)
    sources = case.get("relevant_sources")
    if reviewed and isinstance(sources, list) and evidence:
        metrics["citation_source_precision"] = sum(
            any(_source_matches(item.get("citation", {}), source) for source in sources)
            for item in evidence
        ) / len(evidence)
    image_gold = case.get("relevant_asset_ids")
    if reviewed and isinstance(image_gold, list):
        relevant_images = set(map(str, image_gold))
        hits = len(asset_ids & relevant_images)
        metrics["image_precision"] = hits / len(asset_ids) if asset_ids else None
        metrics["image_recall"] = hits / len(relevant_images) if relevant_images else None
    return {
        "annotation_status": case.get("annotation_status", "needs_review"),
        "evidence_count": len(evidence), "unique_chunk_count": len(chunk_ids),
        "image_count": len(asset_ids), "metrics": metrics,
        "pipeline": retrieval.get("pipeline", {}),
    }


def _source_matches(citation: dict[str, Any], gold: dict[str, Any]) -> bool:
    if not gold.get("document_id") or str(citation.get("document_id")) != str(gold["document_id"]):
        return False
    # Null gold page means the annotation accepts any page of this document.
    start = gold.get("page_start")
    if start is None:
        return True
    end = gold.get("page_end", start)
    actual_start = citation.get("page_start")
    actual_end = citation.get("page_end") or actual_start
    return (isinstance(actual_start, int) and isinstance(actual_end, int)
            and isinstance(end, int) and actual_start <= end and actual_end >= start)


def summarize_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row.get("metrics", {}).items():
            if value is not None:
                values.setdefault(key, []).append(float(value))
    return {key: {"mean": sum(items) / len(items), "scored_cases": len(items)}
            for key, items in values.items()}
