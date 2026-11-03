"""Convert a PPT/PPTX locally to MinerU-compatible PDF segment(s).

The source presentation never leaves this machine.  The emitted manifest preserves
source and derivative SHA-256 values and page ranges, so an external parser job can
be traced back to the exact original presentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader, PdfWriter

DEFAULT_MAX_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_PAGES_PER_SEGMENT = 5
GENERATOR_VERSION = "pptx-pdf-normalizer-v2"
DEFAULT_SOFFICE = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mib(value: int) -> float:
    return round(value / 1024 / 1024, 2)


def require_soffice(configured: str | None) -> Path:
    if configured:
        candidate = Path(configured)
    else:
        from_path = shutil.which("soffice") or shutil.which("soffice.exe")
        candidate = Path(from_path) if from_path else DEFAULT_SOFFICE
    if not candidate.is_file():
        raise FileNotFoundError(
            "LibreOffice soffice executable was not found. Install LibreOffice or pass --soffice."
        )
    return candidate


def convert_to_pdf(source: Path, output_dir: Path, soffice: Path, timeout_seconds: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_root = Path(tempfile.mkdtemp(prefix="design-rag-lo-", dir=output_dir))
    try:
        profile_url = profile_root.resolve().as_uri()
        command = [
            str(soffice),
            "--headless",
            f"-env:UserInstallation={profile_url}",
            "--convert-to",
            "pdf:impress_pdf_Export",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        expected = output_dir / f"{source.stem}.pdf"
        if completed.returncode != 0 or not expected.is_file() or expected.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"LibreOffice PDF export failed ({completed.returncode}): {detail}")
        return expected
    finally:
        shutil.rmtree(profile_root, ignore_errors=True)


def write_page_range(reader: PdfReader, start_index: int, end_index: int, target: Path) -> None:
    writer = PdfWriter()
    for index in range(start_index, end_index):
        writer.add_page(reader.pages[index])
    with target.open("wb") as stream:
        writer.write(stream)


def split_pdf(
    pdf_path: Path,
    segments_dir: Path,
    max_bytes: int,
    max_pages_per_segment: int = DEFAULT_MAX_PAGES_PER_SEGMENT,
) -> list[dict[str, object]]:
    """Split a converted presentation into bounded, traceable PDF segments."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if max_pages_per_segment <= 0:
        raise ValueError("max_pages_per_segment must be positive")
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    if total_pages < 1:
        raise RuntimeError("Converted PDF contains no pages")
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict[str, object]] = []
    start_index = 0
    while start_index < total_pages:
        max_end = min(total_pages, start_index + max_pages_per_segment)
        last_valid_end: int | None = None
        for end_index in range(start_index + 1, max_end + 1):
            candidate = segments_dir / ".candidate.pdf"
            write_page_range(reader, start_index, end_index, candidate)
            fits = candidate.stat().st_size <= max_bytes
            candidate.unlink(missing_ok=True)
            if not fits:
                break
            last_valid_end = end_index
        if last_valid_end is None:
            raise RuntimeError(f"Page {start_index + 1} alone exceeds the configured MinerU size limit")
        target = segments_dir / f"{pdf_path.stem}-pages-{start_index + 1:04d}-{last_valid_end:04d}.pdf"
        write_page_range(reader, start_index, last_valid_end, target)
        segments.append({
            "file_path": str(target), "page_start": start_index + 1, "page_end": last_valid_end,
            "bytes": target.stat().st_size, "sha256": sha256(target), "generator_version": GENERATOR_VERSION,
        })
        start_index = last_valid_end
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a presentation to PDF and split it into MinerU-compatible page ranges."
    )
    parser.add_argument("source", type=Path, help="Local .ppt or .pptx source file")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for the PDF and manifest")
    parser.add_argument("--soffice", help="Path to LibreOffice soffice.exe")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-pages-per-segment", type=int, default=DEFAULT_MAX_PAGES_PER_SEGMENT)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    source = args.source.resolve()
    if source.suffix.lower() not in {".ppt", ".pptx"}:
        raise SystemExit("source must be a .ppt or .pptx file")
    if not source.is_file():
        raise SystemExit(f"source does not exist: {source}")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")
    if args.max_pages_per_segment <= 0:
        raise SystemExit("--max-pages-per-segment must be positive")

    out_dir = args.out_dir.resolve()
    converted_dir = out_dir / "converted"
    segments_dir = out_dir / "mineru-segments"
    converted_pdf = convert_to_pdf(source, converted_dir, require_soffice(args.soffice), args.timeout_seconds)
    segments = split_pdf(converted_pdf, segments_dir, args.max_bytes, args.max_pages_per_segment)
    manifest = {
        "run_kind": "pptx_pdf_normalization",
        "generator_version": GENERATOR_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "file_path": str(source),
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        },
        "converted_pdf": {
            "file_path": str(converted_pdf),
            "bytes": converted_pdf.stat().st_size,
            "sha256": sha256(converted_pdf),
            "page_count": len(PdfReader(str(converted_pdf)).pages),
        },
        "mineru_max_bytes": args.max_bytes,
        "max_pages_per_segment": args.max_pages_per_segment,
        "segments": segments,
    }
    manifest_path = out_dir / "pptx-to-pdf-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "manifest": str(manifest_path),
        "page_count": manifest["converted_pdf"]["page_count"],
        "converted_pdf_mib": mib(converted_pdf.stat().st_size),
        "segment_count": len(segments),
        "segment_mib": [mib(int(item["bytes"])) for item in segments],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

