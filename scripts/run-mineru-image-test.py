"""Run one explicit, auditable MinerU image-ingestion test set.

The script is intentionally opt-in: it uploads nothing unless --submit is supplied.
It records only source paths, SHA-256 values, Backend job IDs and final states in
an on-disk manifest; it never reads or writes API tokens or signed object URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

STYLE_SETS: tuple[tuple[str, str, Path], ...] = (
    (
        "style-eclectic-decorative",
        "风格01折衷装饰",
        Path(r"C:\Users\liwj\AppData\Local\Temp\BNZ.6a857b516e7a565\风格01折衷装饰"),
    ),
    (
        "style-eclectic-minimal",
        "风格02折衷极简",
        Path(r"C:\Users\liwj\AppData\Local\Temp\BNZ.6a857b516e7a565\风格02折衷极简"),
    ),
    (
        "style-eclectic-classical",
        "风格03折衷古典",
        Path(r"C:\Users\liwj\AppData\Local\Temp\BNZ.6a857b516e7a565\风格03折衷古典"),
    ),
)
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def discover() -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for project_key, project_name, directory in STYLE_SETS:
        if not directory.is_dir():
            raise FileNotFoundError(f"Test source folder not found: {directory}")
        for source in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if source.is_file() and source.suffix.lower() in ALLOWED_IMAGE_SUFFIXES:
                files.append(
                    {
                        "project_external_key": project_key,
                        "project_name": project_name,
                        "source_path": str(source),
                        "file_name": source.name,
                        "file_size_bytes": source.stat().st_size,
                        "source_sha256": sha256(source),
                    }
                )
    return files


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def post_json(client: httpx.Client, url: str, *, json_body: dict[str, object] | None = None) -> dict[str, Any]:
    response = client.post(url, json=json_body)
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"Request rejected ({response.status_code}) at {url}: {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected non-object response from {url}")
    return payload


def create_and_submit(client: httpx.Client, backend_url: str, item: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(item["source_path"]))
    document_key = f"{item['project_external_key']}-{source.stem.lower()}"
    metadata = {
        "dataset": "mineru-style-image-test-2026-08-19",
        "style_label": item["project_name"],
        "source_file_name": source.name,
        "test_data": True,
    }
    with source.open("rb") as file_handle:
        response = client.post(
            f"{backend_url}/api/v1/ingestion-jobs",
            data={
                "project_external_key": str(item["project_external_key"]),
                "project_name": str(item["project_name"]),
                "document_external_key": document_key,
                "access_scope_json": "{}",
                "document_metadata_json": json.dumps(metadata, ensure_ascii=False),
                "processing_profile": "ocr_and_vision",
            },
            files={
                "file": (
                    source.name,
                    file_handle,
                    CONTENT_TYPES[source.suffix.lower()],
                )
            },
        )
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"Job creation rejected ({response.status_code}): {detail}")
    created = response.json()
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError(f"Ingestion job creation returned no job ID for {source.name}")
    submitted = post_json(
        client,
        f"{backend_url}/api/v1/ingestion-jobs/{created['id']}/submit",
        json_body={"explicit_external_export_approved": True},
    )
    return {
        **item,
        "job_id": str(created["id"]),
        "created_status": created.get("status"),
        "submitted_status": submitted.get("status"),
        "provider_task_id": submitted.get("provider_task_id"),
        "provider_batch_id": submitted.get("provider_batch_id"),
    }


def poll_and_materialize(
    client: httpx.Client,
    backend_url: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(item["job_id"])
    polled = post_json(client, f"{backend_url}/api/v1/ingestion-jobs/{job_id}/poll")
    status = str(polled.get("status", "unknown"))
    item["last_status"] = status
    item["error_code"] = polled.get("error_code")
    item["error_message"] = polled.get("error_message")
    if status == "succeeded":
        materialized = post_json(client, f"{backend_url}/api/v1/ingestion-jobs/{job_id}/materialize")
        item["materialized"] = bool(materialized.get("materialized"))
        ingestion = materialized.get("ingestion")
        if isinstance(ingestion, dict):
            item["document_version_id"] = ingestion.get("document_version_id")
            item["index_status"] = ingestion.get("index_status")
    return item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8030")
    parser.add_argument("--submit", action="store_true", help="Explicitly permit files to be sent to MinerU.")
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument(
        "--continue-on-submission-error",
        action="store_true",
        help="Continue to later files after a submission error. Disabled by default to avoid repeated failed exports.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("artifacts") / f"mineru-style-test-{datetime.now():%Y%m%d-%H%M%S}.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = discover()
    counts = Counter(item["project_external_key"] for item in files)
    print(f"Discovered {len(files)} images: {dict(counts)}")
    if not args.submit:
        print("Dry run only. Use --submit only after explicit authorization to export files to MinerU.")
        return 0

    backend_url = args.backend_url.rstrip("/")
    manifest: dict[str, Any] = {
        "run_name": "mineru-style-image-test-2026-08-19",
        "started_at": datetime.now().astimezone().isoformat(),
        "backend_url": backend_url,
        "explicit_export_authorization": "REPLACE_ME",
        "items": [],
    }
    save_manifest(args.manifest, manifest)

    with httpx.Client(timeout=180.0) as client:
        ready = client.get(f"{backend_url}/ready")
        ready.raise_for_status()
        for index, item in enumerate(files, start=1):
            print(f"[{index}/{len(files)}] submit {item['project_name']} / {item['file_name']}", flush=True)
            try:
                created = create_and_submit(client, backend_url, item)
            except (httpx.HTTPError, OSError, RuntimeError) as error:
                created = {**item, "submission_error": f"{type(error).__name__}: {error}"}
                print(f"  submission failed: {type(error).__name__}", file=sys.stderr, flush=True)
            manifest["items"].append(created)
            save_manifest(args.manifest, manifest)
            if created.get("submission_error") and not args.continue_on_submission_error:
                print("Stopping after the first submission error; no later files will be exported.", file=sys.stderr, flush=True)
                break

        pending = [item for item in manifest["items"] if item.get("job_id") and not item.get("submission_error")]
        deadline = time.monotonic() + args.timeout_seconds
        while pending and time.monotonic() < deadline:
            next_pending: list[dict[str, Any]] = []
            for item in pending:
                try:
                    result = poll_and_materialize(client, backend_url, item)
                except (httpx.HTTPError, OSError, RuntimeError) as error:
                    result = {**item, "poll_error": f"{type(error).__name__}: {error}"}
                if result.get("last_status") not in {"succeeded", "failed", "cancelled"} and not result.get("poll_error"):
                    next_pending.append(result)
            pending = next_pending
            save_manifest(args.manifest, manifest)
            totals = Counter(str(item.get("last_status", "submission_failed")) for item in manifest["items"])
            print(f"Polling status: {dict(totals)}; pending={len(pending)}", flush=True)
            if pending:
                time.sleep(args.poll_interval_seconds)

    if pending:
        for item in pending:
            item["timeout"] = True
        save_manifest(args.manifest, manifest)
    manifest["completed_at"] = datetime.now().astimezone().isoformat()
    manifest["summary"] = dict(Counter(
        "submission_failed" if item.get("submission_error") else str(item.get("last_status", "unknown"))
        for item in manifest["items"]
    ))
    save_manifest(args.manifest, manifest)
    print(f"Manifest: {args.manifest.resolve()}")
    print(f"Summary: {manifest['summary']}")
    return 0 if not pending else 2


if __name__ == "__main__":
    raise SystemExit(main())
