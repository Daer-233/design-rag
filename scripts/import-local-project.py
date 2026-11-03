"""Import a local Design_RAG project without exporting source data externally.

The importer always archives supported files and records an authoritative PostgreSQL
job/document/asset record. PDFs are additionally text-extracted with local pypdf and
indexed through the normal parent-child ingestion API. Image files remain archived but
unindexed unless a separately approved local vision/OCR pipeline is available; no GPT,
MinerU, or remote service is called by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader
import pypdf

SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover(source_dir: Path) -> list[dict[str, Any]]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")
    records: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        records.append(
            {
                "source_path": str(path),
                "relative_path": path.relative_to(source_dir).as_posix(),
                "file_name": path.name,
                "extension": path.suffix.lower(),
                "file_size_bytes": path.stat().st_size,
                "source_sha256": sha256(path),
                "asset_kind": "rendering" if path.suffix.lower() in IMAGE_EXTENSIONS else "source_document",
            }
        )
    return records


def archive_source(
    client: httpx.Client,
    backend_url: str,
    item: dict[str, Any],
    project_external_key: str,
    project_name: str,
) -> dict[str, Any]:
    source = Path(item["source_path"])
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    document_external_key = f"{project_external_key}-{item['source_sha256'][:16]}"
    metadata = {
        "source_relative_path": item["relative_path"],
        "asset_kind": item["asset_kind"],
        "source_sha256": item["source_sha256"],
        "import_pipeline": "local_archive_and_pypdf_text_v1",
        "external_calls": False,
    }
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
    result = response.json()
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError("Unexpected ingestion-job response")
    return result


def split_text(text: str, limit: int = 1200, overlap: int = 150) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(normalized) <= limit:
        return [normalized] if normalized else []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + limit)
        if end < len(normalized):
            boundary = max(normalized.rfind("\n", start + limit // 2, end), normalized.rfind("。", start + limit // 2, end))
            if boundary > start:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def pdf_ingest_payload(
    item: dict[str, Any],
    job: dict[str, Any],
    project_external_key: str,
    project_name: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    source = Path(item["source_path"])
    reader = PdfReader(str(source))
    parents: list[dict[str, Any]] = []
    indexed_pages = 0
    extracted_chars = 0
    skipped_pages = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if len(text) < 20:
            skipped_pages += 1
            continue
        extracted_chars += len(text)
        indexed_pages += 1
        page_content = f"PDF 第 {page_number} 页\n\n{text}"
        locator = {
            "kind": "pdf_page",
            "page": page_number,
            "source_sha256": item["source_sha256"],
            "source_relative_path": item["relative_path"],
            "extraction": "local_pypdf_text",
        }
        children = [
            {
                "ordinal": child_ordinal,
                "content": child_text,
                "chunk_type": "pdf_text",
                "metadata": {"source_sha256": item["source_sha256"], "extraction": "local_pypdf_text"},
                "page_start": page_number,
                "page_end": page_number,
                "section_path": f"PDF 第 {page_number} 页",
                "source_locator": {**locator, "child_ordinal": child_ordinal},
                "review_status": "pending",
                "source_confidence": 1.0,
            }
            for child_ordinal, child_text in enumerate(split_text(text))
        ]
        parents.append(
            {
                "ordinal": len(parents),
                "content": page_content,
                "chunk_type": "pdf_page",
                "metadata": {
                    "source_sha256": item["source_sha256"],
                    "extraction": "local_pypdf_text",
                    "pypdf_page_index": page_number,
                },
                "page_start": page_number,
                "page_end": page_number,
                "section_path": f"PDF 第 {page_number} 页",
                "source_locator": locator,
                "access_scope": {},
                "review_status": "pending",
                "source_confidence": 1.0,
                "children": children,
            }
        )
    source_uri = str(job.get("source_uri") or f"local://imported/{item['source_sha256']}/original/{item['file_name']}")
    payload = {
        "project": {
            "external_key": project_external_key,
            "name": project_name,
            "metadata": {"import_pipeline": "local_archive_and_pypdf_text_v1", "external_calls": False},
        },
        "document": {
            "external_key": f"{project_external_key}-{item['source_sha256'][:16]}",
            "source_uri": source_uri,
            "file_name": item["file_name"],
            "file_type": "pdf",
            "metadata": {
                "source_relative_path": item["relative_path"],
                "asset_kind": "source_document",
                "source_sha256": item["source_sha256"],
                "source_size_bytes": item["file_size_bytes"],
                "local_parser": "pypdf",
                "external_calls": False,
            },
            "access_scope": {},
        },
        "parser": {
            "provider": "local_pypdf",
            "model": "pypdf",
            "version": getattr(pypdf, "__version__", "unknown"),
            "parsed_content_uri": f"local://parsed/pypdf/{item['source_sha256']}.json",
            "parsed_payload": {
                "page_count": len(reader.pages),
                "indexed_pages": indexed_pages,
                "skipped_low_text_pages": skipped_pages,
                "extracted_text_characters": extracted_chars,
                "external_calls": False,
            },
            "source_confidence": 1.0,
        },
        "chunks": parents,
    }
    return payload, {"page_count": len(reader.pages), "indexed_pages": indexed_pages, "skipped_low_text_pages": skipped_pages, "extracted_text_characters": extracted_chars}


def index_pdf_text(client: httpx.Client, backend_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"{backend_url.rstrip('/')}/api/v1/documents/ingest", json=payload)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected document-ingest response")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--project-external-key", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8030")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    items = discover(args.source_dir)
    if not items:
        raise SystemExit("No supported files found")
    report_items: list[dict[str, Any]] = []
    with httpx.Client(timeout=900) as client:
        for index, item in enumerate(items, start=1):
            record = dict(item)
            print(f"[{index}/{len(items)}] archive {item['relative_path']}", flush=True)
            try:
                job = archive_source(client, args.backend_url, item, args.project_external_key, args.project_name)
                record["ingestion_job_id"] = job["id"]
                record["document_id"] = job.get("document_id")
                record["archival_status"] = job.get("status")
                record["external_submission_performed"] = False
                if item["extension"] == ".pdf":
                    payload, extraction = pdf_ingest_payload(item, job, args.project_external_key, args.project_name)
                    record["local_pdf_extraction"] = extraction
                    if payload["chunks"]:
                        ingest = index_pdf_text(client, args.backend_url, payload)
                        record["index_status"] = ingest.get("index_status")
                        record["document_version_id"] = ingest.get("document_version_id")
                        record["parent_chunk_count"] = len(ingest.get("parent_chunk_ids", []))
                        record["child_chunk_count"] = len(ingest.get("child_chunk_ids", []))
                    else:
                        record["index_status"] = "not_indexed_no_extractable_pdf_text"
                elif item["extension"] in IMAGE_EXTENSIONS:
                    record["index_status"] = "archived_not_indexed_local_vision_or_ocr_required"
                    record["reason"] = "No image bytes were sent to GPT, MinerU, or any external service."
                else:
                    record["index_status"] = "archived_not_indexed_local_parser_not_implemented"
            except (OSError, ValueError, httpx.HTTPError, RuntimeError) as error:
                record["error"] = f"{type(error).__name__}: {error}"
            report_items.append(record)

    summary = {
        "supported_files": len(report_items),
        "archived": sum("ingestion_job_id" in item for item in report_items),
        "failed": sum("error" in item for item in report_items),
        "pdf_indexed": sum(item.get("index_status") == "indexed" for item in report_items),
        "images_archived_pending_local_vision_or_ocr": sum(item.get("index_status") == "archived_not_indexed_local_vision_or_ocr_required" for item in report_items),
        "external_calls": False,
    }
    report = {
        "run_kind": "local_project_import",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(args.source_dir),
        "project_external_key": args.project_external_key,
        "project_name": args.project_name,
        "backend_url": args.backend_url,
        "summary": summary,
        "items": report_items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"Wrote {args.output}", flush=True)
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
