import importlib.util
from pathlib import Path
from pypdf import PdfWriter

SCRIPT = Path(__file__).parents[3] / "scripts" / "normalize-pptx-for-mineru.py"
spec = importlib.util.spec_from_file_location("pptx_normalizer", SCRIPT)
assert spec and spec.loader
pptx_normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pptx_normalizer)


def test_pdf_split_is_bounded_to_five_pages_and_has_source_hashes(tmp_path: Path) -> None:
    source = tmp_path / "slides.pdf"
    writer = PdfWriter()
    for _ in range(12):
        writer.add_blank_page(width=200, height=200)
    with source.open("wb") as stream:
        writer.write(stream)
    segments = pptx_normalizer.split_pdf(source, tmp_path / "segments", 200 * 1024 * 1024)
    assert [(item["page_start"], item["page_end"]) for item in segments] == [(1, 5), (6, 10), (11, 12)]
    assert all(Path(str(item["file_path"])).is_file() for item in segments)
    assert all(item["sha256"] and item["generator_version"] == "pptx-pdf-normalizer-v2" for item in segments)
