from pathlib import Path

from app.main import _has_valid_admin_web_build


def test_admin_web_build_requires_a_normal_html_entrypoint(tmp_path: Path) -> None:
    missing_build = tmp_path / "missing"
    assert _has_valid_admin_web_build(missing_build) is False

    invalid_build = tmp_path / "invalid"
    invalid_build.mkdir()
    (invalid_build / "index.html").write_bytes(b"\x88\x00\xff\x01")
    assert _has_valid_admin_web_build(invalid_build) is False

    valid_build = tmp_path / "valid"
    valid_build.mkdir()
    (valid_build / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    assert _has_valid_admin_web_build(valid_build) is True