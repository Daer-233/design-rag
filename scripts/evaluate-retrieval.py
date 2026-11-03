"""Export local suites, capture 6/20/40 retrieval runs, or score captures offline.

No answer model or RAGAS evaluator is called. Database access is read-only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/backend"))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def corpus_snapshot(project_id):
    from uuid import UUID
    import psycopg
    from app.core.config import settings
    from app.db.connection import normalized_postgres_dsn
    digest = sha256()
    with psycopg.connect(normalized_postgres_dsn(settings.postgres_dsn), connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        rows = conn.execute("""
            SELECT c.id, md5(to_jsonb(c)::text || to_jsonb(d)::text || to_jsonb(v)::text)
            FROM chunks c JOIN document_versions v ON v.id=c.document_version_id
            JOIN documents d ON d.id=v.document_id WHERE d.project_id=%s ORDER BY c.id
        """, (UUID(project_id),)).fetchall()
        for row in rows:
            digest.update(json.dumps(row, default=str).encode())
        images = conn.execute("""
            SELECT a.id, md5(to_jsonb(a)::text) FROM image_assets a
            JOIN documents d ON d.id=a.document_id WHERE d.project_id=%s ORDER BY a.id
        """, (UUID(project_id),)).fetchall()
        for row in images:
            digest.update(json.dumps(row, default=str).encode())
    return {"sha256": digest.hexdigest(), "chunk_count": len(rows), "image_count": len(images)}


def export_suite(args):
    from app.repositories.evaluation_repository import EvaluationRepository
    suite = EvaluationRepository().get_suite(args.suite_id)
    if not suite:
        raise ValueError("Evaluation suite not found")
    result = {
        "schema_version": 1, "suite_id": str(suite["id"]),
        "project_id": str(suite["project_id"]), "principal_roles": [],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "corpus_snapshot": corpus_snapshot(str(suite["project_id"])),
        "notes": "Imported reference answers are unverified. Add evidence labels and review each case before scoring.",
        "cases": [{"id": str(case["id"]), "question": case["question"],
                   "reference_answer": case["reference_answer"], "enabled": case["enabled"],
                   "annotation_status": "needs_review", "category": None,
                   "relevant_chunk_ids": None, "relevant_sources": None, "relevant_asset_ids": None}
                  for case in suite["cases"]],
    }
    write_json(args.output, result)
    print(json.dumps({"exported_cases": len(result["cases"]), "reviewed_cases": 0,
                      "corpus_snapshot": result["corpus_snapshot"]}))


def capture(args):
    from app.clients.inference import EmbeddingClient, RerankClient
    from app.core.config import settings
    from app.repositories.rag_repository import RagRepository
    from app.services.access_policy import AccessPolicy, Principal
    from app.services.rag_service import SearchService
    suite = read_json(args.suite)
    # This first-stage command is intentionally limited to existing local services.
    for url in [settings.embedding_service_url, settings.rerank_service_url, settings.qdrant_url]:
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Capture requires loopback inference and vector services")
    before = corpus_snapshot(suite["project_id"])
    if before != suite["corpus_snapshot"]:
        raise ValueError("Corpus changed since suite export; review labels and export a new version")
    report = {"schema_version": 1, "suite_sha256": sha256(Path(args.suite).read_bytes()).hexdigest(),
              "corpus_snapshot": before, "k": args.k, "budgets": args.budgets,
              "status": "running", "runs": [], "created_at": datetime.now(timezone.utc).isoformat(),
              "settings": {key: getattr(settings, key) for key in [
                  "retrieval_recall_top_k", "retrieval_coarse_top_k", "retrieval_max_evidence_per_document",
                  "embedding_model_id", "embedding_model_revision", "qdrant_collection"]},
              "code_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in [
                  "apps/backend/app/services/rag_service.py", "apps/backend/app/services/coarse_ranker.py"]}}
    class CountedEmbedding(EmbeddingClient):
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return super().embed(texts)

    class CountedRerank(RerankClient):
        calls = 0

        def rerank(self, query, documents):
            self.calls += 1
            return super().rerank(query, documents)

    embedding, rerank = CountedEmbedding(), CountedRerank()
    try:
        report["models"] = {}
        for name, client in [("embedding", embedding), ("rerank", rerank)]:
            health = client.health()
            report["models"][name] = {key: health[key] for key in [
                "model_id", "model_revision", "pytorch_weights_sha256", "model_weights_sha256",
                "device", "status"] if key in health}
        # Load models before timing corpus queries. These calls contain no corpus data.
        embedding.embed(["warmup"])
        rerank.rerank("warmup", ["warmup"])
        report["warmup"] = {"embedding_calls": 1, "rerank_calls": 1}
        service = SearchService(RagRepository(), embedding, rerank, AccessPolicy())
        principal = Principal("local-retrieval-evaluator", {suite["project_id"]}, set(suite["principal_roles"]))
        previous = settings.retrieval_rerank_candidate_top_k
        try:
            for case in [c for c in suite["cases"] if c.get("enabled", True)][:args.max_cases]:
                for budget in args.budgets:
                    settings.retrieval_rerank_candidate_top_k = budget
                    counts_before = (embedding.calls, rerank.calls)
                    result = service.search(query=case["question"], project_id=suite["project_id"],
                                            principal_raw=principal, evidence_top_k=args.k,
                                            redact_source_details=True)
                    # Persist identifiers/locators, never signed image URLs or source text.
                    report["runs"].append({"case_id": case["id"], "budget": budget,
                        "results": [{"chunk_id": item["chunk_id"], "citation": {
                            key: item.get("citation", {}).get(key) for key in [
                                "document_id", "file_name", "page_start", "page_end"]}}
                            for item in result["results"]],
                        "images": [{"asset_id": item["asset_id"]} for item in result.get("images", [])],
                        "pipeline": result["pipeline"], "model_calls": {
                            "embedding": embedding.calls - counts_before[0],
                            "rerank": rerank.calls - counts_before[1], "external_llm": 0}})
                    write_json(args.output, report)
        finally:
            settings.retrieval_rerank_candidate_top_k = previous
        if corpus_snapshot(suite["project_id"]) != before:
            raise ValueError("Corpus changed during capture; results are not comparable")
        report["status"] = "succeeded"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        # Exception strings may contain host paths or connection credentials.
        write_json(args.output, report)
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    write_json(args.output, report)
    print(json.dumps({"status": report["status"], "captured_runs": len(report["runs"])}))
    return 0


def score(args):
    from app.services.retrieval_evaluation import score_retrieval, summarize_scores
    suite, captured = read_json(args.suite), read_json(args.captures)
    if captured["status"] != "succeeded":
        raise ValueError("Cannot score failed/incomplete captures")
    if captured["suite_sha256"] != sha256(Path(args.suite).read_bytes()).hexdigest():
        raise ValueError("Suite changed since capture; collect a matching version")
    if captured["corpus_snapshot"] != suite["corpus_snapshot"]:
        raise ValueError("Corpus snapshots do not match")
    cases = {case["id"]: case for case in suite["cases"]}
    pairs = [(run["case_id"], run["budget"]) for run in captured["runs"]]
    budgets = set(captured["budgets"])
    captured_cases = {case_id for case_id, _ in pairs}
    if (not pairs or len(set(pairs)) != len(pairs)
            or not captured_cases.issubset(cases)
            or set(pairs) != {(case_id, budget) for case_id in captured_cases for budget in budgets}):
        raise ValueError("Capture must contain the same cases once for every budget")
    rows = [{"case_id": run["case_id"], "budget": run["budget"],
             **score_retrieval(cases[run["case_id"]], run, captured["k"])} for run in captured["runs"]]
    groups = {}
    from statistics import mean, median
    for budget in captured["budgets"]:
        group = [row for row in rows if row["budget"] == budget]
        timings = [row["pipeline"].get("performance_ms", {}).get("total") for row in group]
        timings = [value for value in timings if isinstance(value, (int, float))]
        groups[str(budget)] = {
            "quality": summarize_scores(group), "run_count": len(group),
            "total_ms_mean": mean(timings) if timings else None,
            "total_ms_median": median(timings) if timings else None,
            "evidence_count_mean": mean(row["evidence_count"] for row in group) if group else None,
            "model_calls": {key: sum(run.get("model_calls", {}).get(key, 0) for run in captured["runs"]
                                      if run["budget"] == budget)
                            for key in ["embedding", "rerank", "external_llm"]},
        }
    write_json(args.output, {"schema_version": 1, "suite_sha256": captured["suite_sha256"],
                            "k": captured["k"], "summary_by_budget": groups, "cases": rows})
    print(json.dumps({"scored_runs": len(rows), "summary_by_budget": groups}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--suite-id", required=True)
    for name in ["capture", "score"]:
        child = sub.add_parser(name)
        child.add_argument("--suite", type=Path, required=True)
        if name == "capture":
            child.add_argument("--budgets", nargs="+", type=int, default=[6, 20, 40])
            child.add_argument("--k", type=int, default=8)
            child.add_argument("--max-cases", type=int, default=3)
        else:
            child.add_argument("--captures", type=Path, required=True)
    for child in sub.choices.values():
        child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture" and (not 1 <= args.k <= 50 or not 1 <= args.max_cases <= 30
                                     or any(not 1 <= b <= 100 for b in args.budgets)):
        parser.error("k=1..50, max-cases=1..30, budgets=1..100")
    return {"export": export_suite, "capture": capture, "score": score}[args.command](args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
