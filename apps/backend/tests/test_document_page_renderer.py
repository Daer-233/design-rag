from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.core.config import settings
from app.services.document_page_renderer import DocumentPageRenderer


def test_document_page_renderer_rasterizes_every_pdf_page_without_exif(tmp_path: Path) -> None:
    if not settings.pdftoppm_path.is_file():
        pytest.skip(f"pdftoppm is not installed at {settings.pdftoppm_path}")
    source = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=842, height=595)
    with source.open("wb") as stream:
        writer.write(stream)
    renderer = DocumentPageRenderer(
        soffice_path=settings.soffice_path,
        pdftoppm_path=settings.pdftoppm_path,
        dpi=72,
        max_pages=5,
        timeout_seconds=60,
    )
    source_sha256 = renderer._sha256(source)
    rendered = renderer.render(source, output_root=tmp_path / "rendered", source_sha256=source_sha256)
    assert rendered.page_count == 2
    assert rendered.converted_to_pdf is False
    assert [page.page_number for page in rendered.pages] == [1, 2]
    assert all(len(page.sha256) == 64 and page.size_bytes > 0 for page in rendered.pages)
    assert rendered.pages[0].width < rendered.pages[0].height
    assert rendered.pages[1].width > rendered.pages[1].height
    for page in rendered.pages:
        with Image.open(page.image_path) as image:
            assert image.format == "JPEG"
            assert image.getexif() == {}