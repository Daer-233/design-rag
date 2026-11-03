"""Audit a completed MinerU image-test manifest without exposing secrets or signed URLs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import settings
from app.db.connection import connection


def manifest_summary(items: list[dict[str, object]]) -> dict[str, object]:
    expected_by_project: dict[str, dict[str, int]] = defaultdict(lambda: Counter())
    failure_reasons: Counter[str] = Counter()
    for item in items:
        project = str(item["project_external_key"])
        status = str(item.get("last_status") or "unknown")
        expected_by_project[project]["submitted"] += 1
        expected_by_project[project][status] += 1
        if status == "succeeded" and item.get("materialized") is True:
            expected_by_project[project]["materialized"] += 1
        if status == "failed" and item.get("error_message"):
            failure_reasons[str(item["error_message"])] += 1
    return {
        "by_project": {key: dict(value) for key, value in expected_by_project.items()},
        "failure_reasons": dict(failure_reasons),
    }


def json_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: (str(value) if isinstance(value, UUID) else value) for key, value in row.items()} for row in rows]


def query_postgres(job_ids: list[str]) -> dict[str, object]:
    job_id_param = (job_ids,)
    with connection() as conn:
        job_rows = conn.execute(
            """
            SELECT p.external_key AS project_external_key, p.name AS project_name,
                   j.status, COUNT(*)::int AS count
            FROM ingestion_jobs j
            JOIN documents d ON d.id = j.document_id
            JOIN projects p ON p.id = d.project_id
            WHERE j.id = ANY(%s::uuid[])
            GROUP BY p.external_key, p.name, j.status
            ORDER BY p.external_key, j.status
            """,
            job_id_param,
        ).fetchall()
        materialization_rows = conn.execute(
            """
            WITH selected_jobs AS (
                SELECT j.id, j.document_id, j.document_version_id
                FROM ingestion_jobs j
                WHERE j.id = ANY(%s::uuid[])
                  AND j.status = 'succeeded'
                  AND j.document_version_id IS NOT NULL
            )
            SELECT p.id::text AS project_id, p.external_key AS project_external_key,
                   p.name AS project_name,
                   COUNT(DISTINCT sj.document_id)::int AS documents,
                   COUNT(DISTINCT sj.document_version_id)::int AS document_versions,
                   COUNT(c.id) FILTER (WHERE c.chunk_level = 'parent')::int AS parent_chunks,
                   COUNT(c.id) FILTER (WHERE c.chunk_level = 'child')::int AS child_chunks,
                   COUNT(v.id) FILTER (WHERE v.index_status = 'indexed')::int AS indexed_vector_records,
                   COUNT(v.id) FILTER (WHERE v.index_status <> 'indexed')::int AS non_indexed_vector_records
            FROM selected_jobs sj
            JOIN documents d ON d.id = sj.document_id
            JOIN projects p ON p.id = d.project_id
            LEFT JOIN chunks c ON c.document_version_id = sj.document_version_id
            LEFT JOIN vector_index_records v ON v.chunk_id = c.id
            GROUP BY p.id, p.external_key, p.name
            ORDER BY p.external_key
            """,
            job_id_param,
        ).fetchall()
        chunk_rows = conn.execute(
            """
            SELECT p.id::text AS project_id, p.external_key AS project_external_key,
                   c.id::text AS chunk_id
            FROM ingestion_jobs j
            JOIN documents d ON d.id = j.document_id
            JOIN projects p ON p.id = d.project_id
            JOIN chunks c ON c.document_version_id = j.document_version_id
            JOIN vector_index_records v ON v.chunk_id = c.id AND v.index_status = 'indexed'
            WHERE j.id = ANY(%s::uuid[])
              AND j.status = 'succeeded'
              AND j.document_version_id IS NOT NULL
            ORDER BY p.external_key, c.id
            """,
            job_id_param,
        ).fetchall()
        asset_rows = conn.execute(
            """
            SELECT p.external_key AS project_external_key, a.asset_kind, COUNT(*)::int AS count
            FROM ingestion_jobs j
            JOIN documents d ON d.id = j.document_id
            JOIN projects p ON p.id = d.project_id
            JOIN document_assets a ON a.ingestion_job_id = j.id
            WHERE j.id = ANY(%s::uuid[])
            GROUP BY p.external_key, a.asset_kind
            ORDER BY p.external_key, a.asset_kind
            """,
            job_id_param,
        ).fetchall()
    return {
        "job_statuses": json_rows(job_rows),
        "materialization": json_rows(materialization_rows),
        "indexed_chunk_ids": json_rows(chunk_rows),
        "assets": json_rows(asset_rows),
    }


def count_qdrant(indexed_chunk_rows: list[dict[str, object]]) -> tuple[dict[str, int], str | None]:
    if not indexed_chunk_rows:
        return {}, None
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    by_project: dict[str, list[UUID]] = defaultdict(list)
    for row in indexed_chunk_rows:
        by_project[str(row["project_external_key"])].append(UUID(str(row["chunk_id"])))
    counts: dict[str, int] = {}
    try:
        for project_key, ids in by_project.items():
            result = client.count(
                collection_name=settings.qdrant_collection,
                count_filter=models.Filter(must=[models.HasIdCondition(has_id=ids)]),
                exact=True,
            )
            counts[project_key] = int(result.count)
    except Exception as error:  # Preserve audit details without leaking service configuration.
        return counts, type(error).__name__
    return counts, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("items")
    if not isinstance(items, list):
        raise SystemExit("Manifest does not contain an items list")
    safe_items = [item for item in items if isinstance(item, dict)]
    job_ids = [str(item["job_id"]) for item in safe_items if item.get("job_id")]
    if not job_ids:
        raise SystemExit("No job IDs found in manifest")

    manifest_stats = manifest_summary(safe_items)
    postgres = query_postgres(job_ids)
    qdrant_counts, qdrant_error = count_qdrant(postgres.pop("indexed_chunk_ids"))

    report = {
        "audit_kind": "mineru_style_image_test",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "manifest_file": args.manifest.name,
        "manifest_summary": manifest.get("summary", {}),
        "manifest_audit": manifest_stats,
        "postgres": postgres,
        "qdrant": {
            "collection_name": settings.qdrant_collection,
            "matched_indexed_points_by_project": qdrant_counts,
            "error_type": qdrant_error,
        },
    }
    report["verification"] = {
        "postgres_job_total": sum(row["count"] for row in report["postgres"]["job_statuses"]),
        "postgres_indexed_vector_total": sum(
            row["indexed_vector_records"] for row in report["postgres"]["materialization"]
        ),
        "qdrant_matched_indexed_point_total": sum(qdrant_counts.values()),
        "qdrant_matches_postgres_indexed_records": (
            qdrant_error is None
            and sum(qdrant_counts.values())
            == sum(row["indexed_vector_records"] for row in report["postgres"]["materialization"])
        ),
    }

    output = args.output or args.manifest.with_name(f"{args.manifest.stem}-audit.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Audit report: {output}")
    print(json.dumps(report["verification"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
