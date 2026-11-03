"""Explicitly submit and materialize already-prepared ingestion jobs from a manifest.

This command is deliberately separate from local job preparation. It sends no source
file to MinerU unless both opt-in flags are present.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(
    client: httpx.Client, method: str, url: str, *, json_body: dict[str, object] | None = None
) -> dict[str, Any]:
    response = client.request(method, url, json=json_body)
    if response.is_error:
        try:
            detail: object = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"Request rejected ({response.status_code}) at {url}: {detail}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected response from {url}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8030")
    parser.add_argument("--poll-interval-seconds", type=float, default=20.0)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--explicit-external-export-approved", action="store_true")
    args = parser.parse_args()

    if not args.submit or not args.explicit_external_export_approved:
        raise SystemExit("External export requires both --submit and --explicit-external-export-approved")
    if args.poll_interval_seconds <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("Polling interval and timeout must be positive")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("items")
    if not isinstance(items, list):
        raise SystemExit("Manifest does not contain an items list")
    selected = [item for item in items if isinstance(item, dict) and item.get("ingestion_job_id")]
    if not selected:
        raise SystemExit("No prepared ingestion jobs were found in the manifest")

    backend_url = args.backend_url.rstrip("/")
    manifest["external_submission_performed"] = True
    manifest["external_submission_authorized_at"] = datetime.now(timezone.utc).isoformat()
    manifest["external_submission_target"] = "MinerU"
    save_manifest(args.manifest, manifest)

    with httpx.Client(timeout=300) as client:
        readiness = client.get(f"{backend_url}/ready")
        readiness.raise_for_status()
        for index, item in enumerate(selected, start=1):
            job_id = str(item["ingestion_job_id"])
            print(f"[{index}/{len(selected)}] submit {item.get('relative_path', job_id)}", flush=True)
            try:
                current = request_json(client, "GET", f"{backend_url}/api/v1/ingestion-jobs/{job_id}")
                current_status = str(current.get("status", "unknown"))
                if current_status in {"created", "failed"}:
                    submitted = request_json(
                        client,
                        "POST",
                        f"{backend_url}/api/v1/ingestion-jobs/{job_id}/submit",
                        json_body={"explicit_external_export_approved": True},
                    )
                    item["last_status"] = submitted.get("status")
                    item["provider_task_id"] = submitted.get("provider_task_id")
                    item["provider_batch_id"] = submitted.get("provider_batch_id")
                else:
                    item["last_status"] = current_status
                item["external_submission_performed"] = True
            except (httpx.HTTPError, RuntimeError) as error:
                item["submission_error"] = f"{type(error).__name__}: {error}"
                item["last_status"] = "submission_failed"
            save_manifest(args.manifest, manifest)

        pending = [item for item in selected if not item.get("submission_error") and item.get("last_status") not in {"succeeded", "failed", "cancelled"}]
        deadline = time.monotonic() + args.timeout_seconds
        while pending and time.monotonic() < deadline:
            next_pending: list[dict[str, Any]] = []
            for item in pending:
                job_id = str(item["ingestion_job_id"])
                try:
                    polled = request_json(client, "POST", f"{backend_url}/api/v1/ingestion-jobs/{job_id}/poll")
                    status = str(polled.get("status", "unknown"))
                    item["last_status"] = status
                    item["error_code"] = polled.get("error_code")
                    item["error_message"] = polled.get("error_message")
                    if status == "succeeded" and not item.get("materialized"):
                        materialized = request_json(client, "POST", f"{backend_url}/api/v1/ingestion-jobs/{job_id}/materialize")
                        item["materialized"] = bool(materialized.get("materialized"))
                        ingestion = materialized.get("ingestion")
                        if isinstance(ingestion, dict):
                            item["document_version_id"] = ingestion.get("document_version_id")
                            item["index_status"] = ingestion.get("index_status")
                    if status not in {"succeeded", "failed", "cancelled"}:
                        next_pending.append(item)
                except (httpx.HTTPError, RuntimeError) as error:
                    item["poll_error"] = f"{type(error).__name__}: {error}"
                save_manifest(args.manifest, manifest)
            pending = next_pending
            states = Counter(str(item.get("last_status", "unknown")) for item in selected)
            print(f"Polling status: {dict(states)}; pending={len(pending)}", flush=True)
            if pending:
                time.sleep(args.poll_interval_seconds)

    if pending:
        for item in pending:
            item["timeout"] = True
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["summary"] = dict(Counter(str(item.get("last_status", "unknown")) for item in selected))
    save_manifest(args.manifest, manifest)
    print(f"Manifest: {args.manifest.resolve()}")
    print(f"Summary: {manifest['summary']}")
    return 0 if not pending else 2


if __name__ == "__main__":
    raise SystemExit(main())
