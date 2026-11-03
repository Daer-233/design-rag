"""Local PPT/PDF page rendering for explicitly approved visual analysis.

This module never calls an external service. Office documents are converted to PDF with
LibreOffice, PDF pages are rasterized with Poppler, and every JPEG is rewritten through
Pillow so metadata/EXIF is removed before it can become a Vision task.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from pypdf import PdfReader


class DocumentPageRenderError(RuntimeError):
    """A local document could not be converted into safe page images."""


@dataclass(frozen=True)
class RenderedDocumentPage:
    page_number: int
    image_path: Path
    sha256: str
    width: int
    height: int
    size_bytes: int
    content_type: str = "image/jpeg"

    def metadata(self) -> dict[str, object]:
        return {
            "source_page_number": self.page_number,
            "derivation_type": "pdf_page_render",
            "derivative_sha256": self.sha256,
            "derivative_dimensions": {"width": self.width, "height": self.height},
            "derivative_content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "exif_removed": True,
        }


@dataclass(frozen=True)
class RenderedDocument:
    source_path: Path
    pdf_path: Path
    source_type: str
    page_count: int
    pages: list[RenderedDocumentPage]
    converted_to_pdf: bool


class DocumentPageRenderer:
    """Render each document page locally into deterministic, EXIF-free JPEG files."""

    OFFICE_EXTENSIONS = frozenset({"ppt", "pptx"})
    SUPPORTED_EXTENSIONS = frozenset({"pdf", "ppt", "pptx"})

    def __init__(
        self,
        *,
        soffice_path: Path,
        pdftoppm_path: Path,
        dpi: int = 144,
        max_pages: int = 300,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.soffice_path = Path(soffice_path)
        self.pdftoppm_path = Path(pdftoppm_path)
        self.dpi = dpi
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds

    def render(self, source_path: Path, *, output_root: Path, source_sha256: str) -> RenderedDocument:
        source_path = source_path.resolve()
        extension = source_path.suffix.lower().lstrip(".")
        if extension not in self.SUPPORTED_EXTENSIONS:
            raise DocumentPageRenderError(f"Unsupported page-render source '.{extension or 'unknown'}'")
        if not source_path.is_file():
            raise DocumentPageRenderError("Archived document source is missing")
        if self._sha256(source_path) != source_sha256:
            raise DocumentPageRenderError("Archived document SHA-256 no longer matches the batch manifest")
        if not self.pdftoppm_path.is_file():
            raise DocumentPageRenderError(f"pdftoppm executable was not found: {self.pdftoppm_path}")

        render_root = (Path(output_root) / source_sha256).resolve()
        pdf_dir = render_root / "pdf"
        page_dir = render_root / "pages"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        page_dir.mkdir(parents=True, exist_ok=True)

        converted = extension in self.OFFICE_EXTENSIONS
        pdf_path = source_path if extension == "pdf" else self._convert_office_to_pdf(source_path, pdf_dir)
        try:
            page_count = len(PdfReader(str(pdf_path)).pages)
        except Exception as error:  # pypdf raises several parser-specific exception types
            raise DocumentPageRenderError(f"Unable to read rendered PDF page count: {type(error).__name__}") from error
        if page_count < 1:
            raise DocumentPageRenderError("Rendered PDF does not contain any pages")
        if page_count > self.max_pages:
            raise DocumentPageRenderError(
                f"Document contains {page_count} pages, exceeding DOCUMENT_PAGE_VISION_MAX_PAGES={self.max_pages}"
            )

        prefix = page_dir / "page"
        rendered_page_paths = self._find_rendered_page_paths(page_dir, page_count)
        if rendered_page_paths is None:
            self._run([
                str(self.pdftoppm_path), "-jpeg", "-r", str(self.dpi), "-jpegopt", "quality=90",
                "-f", "1", "-l", str(page_count), str(pdf_path), str(prefix),
            ], "PDF page rasterization")
            rendered_page_paths = self._find_rendered_page_paths(page_dir, page_count)
        if rendered_page_paths is None:
            available = sorted(path.name for path in page_dir.glob("page-*.jpg"))
            raise DocumentPageRenderError(
                "PDF renderer did not produce one JPEG for every page; "
                f"found: {', '.join(available[:5]) or 'none'}"
            )

        pages = [self._normalize_page(path, number) for number, path in enumerate(rendered_page_paths, start=1)]
        return RenderedDocument(source_path, pdf_path.resolve(), extension, page_count, pages, converted)

    @staticmethod
    def _find_rendered_page_paths(page_dir: Path, page_count: int) -> list[Path] | None:
        """Accept Poppler's version-dependent zero-padded page file names."""
        by_number: dict[int, Path] = {}
        for path in page_dir.glob("page-*.jpg"):
            suffix = path.stem.removeprefix("page-")
            if suffix.isdecimal():
                by_number[int(suffix)] = path
        expected_numbers = range(1, page_count + 1)
        if not all(number in by_number for number in expected_numbers):
            return None
        return [by_number[number] for number in expected_numbers]
    def _convert_office_to_pdf(self, source_path: Path, output_dir: Path) -> Path:
        if not self.soffice_path.is_file():
            raise DocumentPageRenderError(f"LibreOffice executable was not found: {self.soffice_path}")
        expected = output_dir / f"{source_path.stem}.pdf"
        if expected.is_file():
            return expected
        profile_dir = output_dir / "libreoffice-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_uri = "file:///" + quote(profile_dir.resolve().as_posix(), safe="/:")
        self._run([
            str(self.soffice_path), "--headless", f"-env:UserInstallation={profile_uri}",
            "--convert-to", "pdf", "--outdir", str(output_dir), str(source_path),
        ], "LibreOffice PDF conversion")
        if not expected.is_file():
            candidates = sorted(output_dir.glob("*.pdf"))
            if len(candidates) == 1:
                return candidates[0]
            raise DocumentPageRenderError("LibreOffice reported success but no PDF was created")
        return expected

    def _normalize_page(self, path: Path, page_number: int) -> RenderedDocumentPage:
        try:
            with Image.open(path) as opened:
                opened.load()
                normalized = opened.convert("RGB")
                width, height = normalized.size
                temporary = path.with_suffix(".normalized.jpg")
                normalized.save(temporary, format="JPEG", quality=90, optimize=True, progressive=True, exif=b"")
            os.replace(temporary, path)
        except (OSError, ValueError) as error:
            raise DocumentPageRenderError(
                f"Unable to normalize rendered page {page_number}: {type(error).__name__}"
            ) from error
        return RenderedDocumentPage(
            page_number=page_number,
            image_path=path.resolve(),
            sha256=self._sha256(path),
            width=width,
            height=height,
            size_bytes=path.stat().st_size,
        )

    def _run(self, command: list[str], operation: str) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DocumentPageRenderError(f"{operation} failed: {type(error).__name__}") from error
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "no process output").strip()
            raise DocumentPageRenderError(f"{operation} failed (exit {result.returncode}): {message[:1000]}")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

