"""Prepare a local-only, auditable full-project MinerU ingestion test.

By default this script archives files to the local authoritative store and creates
PostgreSQL ingestion jobs only.  It never sends source files to MinerU unless both
--submit and --explicit-external-export-approved are provided.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

SUPPORTED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".png", ".jpg", ".jpeg", ".webp",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def discover(source_dir: Path) -> list[dict[str, Any]]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")
    files: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*"), key=lambda item: str(item).lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        relative_path = path.relative_to(source_dir).as_posix()
        files.append(
            {
                "source_path": str(path),
                "relative_path": relative_path,
                "file_name": path.name,
                "extension": path.suffix.lower(),
                "file_size_bytes": path.stat().st_size,
                "source_sha256": sha256(path),
                "asset_kind": "rendering" if path.suffix.lower() in IMAGE_EXTENSIONS else "source_document",
            }
        )
    return files


def create_job(
    client: httpx.Client,
    backend_url: str,
    item: dict[str, Any],
    *,
    project_external_key: str,
    project_name: str,
) -> dict[str, Any]:
    source = Path(str(item["source_path"]))
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    metadata = {
        "source_relative_path": item["relative_path"],
        "asset_kind": item["asset_kind"],
        "test_fixture": "greentown-xiasha",
    }
    document_external_key = f"{project_external_key}-{str(item['source_sha256'])[:16]}"
    with source.open("rb") as body:
        response = client.post(
            f"{backend_url.rstrip('/')}/api/v1/ingestion-jobs",
            data={
                "project_external_key": project_external_key,
                "project_name": project_name,
                "document_external_key": document_external_key,
                "document_metadata_json": json.dumps(metadata, ensure_ascii=False),
            },
            files={"file": (source.name, body, content_type)},
        )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict) or not value.get("id"):
        raise RuntimeError("Unexpected ingestion-job response")
    return value


def request_json(
    client: httpx.Client, method: str, url: str, *, json_body: dict[str, object] | None = None
) -> dict[str, Any]:
    response = client.request(method, url, json=json_body)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected response from {url}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8030")
    parser.add_argument("--project-external-key", default="greentown-xiasha")
    parser.add_argument("--project-name", default="01绿城下沙")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--explicit-external-export-approved", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.submit and not args.explicit_external_export_approved:
        raise SystemExit("--submit requires --explicit-external-export-approved")

    items = discover(args.source_dir)
    if not items:
        raise SystemExit("No supported source files were found")

    report_items: list[dict[str, Any]] = []
    with httpx.Client(timeout=600) as client:
        for item in items:
            record = dict(item)
            try:
                job = create_job(
                    client,
                    args.backend_url,
                    item,
                    project_external_key=args.project_external_key,
                    project_name=args.project_name,
                )
                record["ingestion_job_id"] = job["id"]
                record["last_status"] = job.get("status")
                record["local_archival_created"] = True
                if args.submit:
                    submitted = request_json(
                        client,
                        "POST",
                        f"{args.backend_url.rstrip('/')}/api/v1/ingestion-jobs/{job['id']}/submit",
                        json_body={"explicit_external_export_approved": True},
                    )
                    record["last_status"] = submitted.get("status")
                    record["external_submission_performed"] = True
                else:
                    record["external_submission_performed"] = False
            except (OSError, httpx.HTTPError, RuntimeError) as error:
                record["error"] = f"{type(error).__name__}: {error}"
                record["local_archival_created"] = False
                record["external_submission_performed"] = False
            report_items.append(record)

    output = args.output or Path("artifacts") / f"{args.project_external_key}-ingestion-prepare.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_kind": "project_ingestion_test",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(args.source_dir),
        "project_external_key": args.project_external_key,
        "project_name": args.project_name,
        "external_submission_performed": args.submit,
        "items": report_items,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    created = sum(1 for item in report_items if item.get("local_archival_created"))
    failed = len(report_items) - created
    print(f"Report: {output}")
    print(json.dumps({"discovered": len(report_items), "prepared": created, "failed": failed, "submitted": int(args.submit) * created}, ensure_ascii=False))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
