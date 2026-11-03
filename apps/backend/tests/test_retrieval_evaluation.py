from app.services.retrieval_evaluation import score_retrieval, summarize_scores

import importlib.util
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_unreviewed_labels_produce_no_quality_scores():
    result = score_retrieval({"relevant_chunk_ids": ["a"]}, {"results": [{"chunk_id": "a"}]}, 8)
    assert all(value is None for value in result["metrics"].values())
    assert summarize_scores([result]) == {}


def test_rank_metrics_do_not_count_duplicate_chunks_twice():
    case = {"annotation_status": "reviewed", "relevant_chunk_ids": ["a", "b"]}
    result = score_retrieval(case, {"results": [{"chunk_id": cid} for cid in ["x", "a", "a"]]}, 3)
    assert result["metrics"]["recall_at_k"] == 0.5
    assert result["metrics"]["evidence_precision_at_k"] == 0.5
    assert result["metrics"]["reciprocal_rank"] == 0.5


def test_citation_checks_document_and_page_not_just_citation_id():
    case = {"annotation_status": "reviewed", "relevant_sources": [
        {"document_id": "d", "page_start": 4, "page_end": 5}]}
    rows = [{"chunk_id": str(i), "citation": {"document_id": doc, "page_start": page, "page_end": page}}
            for i, (doc, page) in enumerate([("d", 4), ("d", 6), ("other", 4)])]
    assert score_retrieval(case, {"results": rows}, 3)["metrics"]["citation_source_precision"] == 1 / 3


def test_reviewed_negative_and_missing_labels_are_distinct():
    case = {"annotation_status": "reviewed", "relevant_chunk_ids": []}
    assert score_retrieval(case, {"results": []}, 8)["metrics"]["empty_result_correctness"] == 1
    assert score_retrieval(case, {"results": [{"chunk_id": "x"}]}, 8)["metrics"]["empty_result_correctness"] == 0
    assert score_retrieval({"annotation_status": "reviewed"}, {"results": []}, 8)["metrics"]["empty_result_correctness"] is None


def test_image_metrics_and_scored_case_counts():
    case = {"annotation_status": "reviewed", "relevant_asset_ids": ["a", "b"]}
    row = score_retrieval(case, {"results": [], "images": [{"asset_id": "a"}, {"asset_id": "x"}]}, 8)
    assert row["metrics"]["image_precision"] == 0.5
    assert row["metrics"]["image_recall"] == 0.5
    summary = summarize_scores([row, score_retrieval({}, {"results": []}, 8)])
    assert summary["image_precision"] == {"mean": 0.5, "scored_cases": 1}


@pytest.mark.parametrize("failure", [None, "failed_capture", "changed_suite", "missing_budget"])
def test_offline_command_checks_manifest_and_writes_only_reviewed_scores(tmp_path, failure):
    script = Path(__file__).resolve().parents[3] / "scripts/evaluate-retrieval.py"
    spec = importlib.util.spec_from_file_location("retrieval_evaluation_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = {"corpus_snapshot": {"sha256": "fixture"}, "cases": [
        {"id": "case", "annotation_status": "reviewed", "relevant_chunk_ids": ["a"]}]}
    suite_path, captures_path, output = (tmp_path / name for name in ["suite.json", "captures.json", "scores.json"])
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    captured = {"status": "failed" if failure == "failed_capture" else "succeeded",
                "suite_sha256": "changed" if failure == "changed_suite" else sha256(suite_path.read_bytes()).hexdigest(),
                "corpus_snapshot": suite["corpus_snapshot"], "k": 8, "budgets": [20],
                "runs": [{"case_id": "case", "budget": 20, "results": [{"chunk_id": "a"}],
                          "pipeline": {"performance_ms": {"total": 50}},
                          "model_calls": {"embedding": 1, "rerank": 1, "external_llm": 0}}]}
    if failure == "missing_budget":
        captured["budgets"].append(40)
    captures_path.write_text(json.dumps(captured), encoding="utf-8")
    args = SimpleNamespace(suite=suite_path, captures=captures_path, output=output)
    if failure:
        with pytest.raises(ValueError):
            module.score(args)
        assert not output.exists()
    else:
        module.score(args)
        result = json.loads(output.read_text(encoding="utf-8"))["summary_by_budget"]["20"]
        assert result["quality"]["recall_at_k"]["mean"] == 1.0
        assert result["total_ms_mean"] == 50
        assert result["model_calls"]["external_llm"] == 0
