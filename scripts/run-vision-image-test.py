"""Prepare or explicitly submit a maximum 20-image GPT visual-analysis test round.

Default behavior is local-only: it creates visual-analysis jobs but never sends images.
Use --submit together with --explicit-external-export-approved only after reviewing
which archived images will be sent to the configured Now Coding gateway.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected API response from {url}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8030")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 21))
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--explicit-external-export-approved", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.submit and not args.explicit_external_export_approved:
        raise SystemExit("--submit requires --explicit-external-export-approved")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("items")
    if not isinstance(items, list):
        raise SystemExit("Manifest does not contain items")
    eligible = [
        item for item in items
        if isinstance(item, dict)
        and item.get("last_status") == "succeeded"
        and item.get("materialized") is True
        and item.get("job_id")
    ]
    # Take a round-robin sample across projects rather than consuming the manifest's
    # first directory. With the current three folders and limit=20 this yields 7/7/6.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in eligible:
        project_key = str(item.get("project_external_key") or "unknown")
        buckets.setdefault(project_key, []).append(item)
    selected: list[dict[str, Any]] = []
    while len(selected) < args.limit and any(buckets.values()):
        for project_key in buckets:
            if buckets[project_key] and len(selected) < args.limit:
                selected.append(buckets[project_key].pop(0))
    if not selected:
        raise SystemExit("No successful, materialized image ingestion jobs were found")

    created: list[dict[str, Any]] = []
    with httpx.Client(timeout=180) as client:
        for item in selected:
            created.append(
                request(
                    client,
                    "POST",
                    f"{args.backend_url.rstrip('/')}/api/v1/visual-analysis-jobs",
                    json={"ingestion_job_id": item["job_id"]},
                )
            )
        submitted: list[dict[str, Any]] = []
        if args.submit:
            for job in created:
                submitted.append(
                    request(
                        client,
                        "POST",
                        f"{args.backend_url.rstrip('/')}/api/v1/visual-analysis-jobs/{job['id']}/submit",
                        json={"explicit_external_export_approved": True},
                    )
                )

    report = {
        "run_kind": "vision_image_test",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "external_submission_performed": args.submit,
        "limit": args.limit,
        "selected_sources": [
            {
                "project_external_key": item.get("project_external_key"),
                "file_name": item.get("file_name"),
                "ingestion_job_id": item["job_id"],
            }
            for item in selected
        ],
        "visual_analysis_job_ids": [job["id"] for job in created],
        "submitted_count": len(submitted),
    }
    output = args.output or args.manifest.with_name(f"{args.manifest.stem}-vision-prepare.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output}")
    print(json.dumps({"prepared": len(created), "submitted": len(submitted)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
