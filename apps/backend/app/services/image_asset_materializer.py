"""Local image-asset materialization helpers.

All operations are local: the source archive is already present on disk and no bytes
are sent to MinerU, GPT, or any other external service.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
from zipfile import ZipFile, is_zipfile

from PIL import Image, ImageOps


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def perceptual_hash(path: Path) -> str | None:
    """Return a deterministic 64-bit grayscale pHash-like value without extra services."""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("L").resize((8, 8))
            values = list(image.getdata())
    except (OSError, ValueError):
        return None
    if not values:
        return None
    average = sum(values) / len(values)
    bits = "".join("1" if value >= average else "0" for value in values)
    return f"{int(bits, 2):016x}"


def materialize_archive_image_assets(
    *,
    result_path: Path,
    output_dir: Path,
    image_assets: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Copy referenced MinerU archive images to internal storage and enrich metadata.

    Missing image files are not an error: the structured region metadata remains
    authoritative and is still persisted with a ``None`` storage key.
    """
    if not is_zipfile(result_path):
        return image_assets
    output_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(result_path) as archive:
        files = [name for name in archive.namelist() if not name.endswith("/")]
        lower_lookup = {name.lower(): name for name in files}
        for index, raw in enumerate(image_assets):
            member_raw = raw.get("archive_member")
            if not isinstance(member_raw, str) or not member_raw.strip():
                continue
            requested = member_raw.replace("\\", "/").lstrip("/")
            member = lower_lookup.get(requested.lower())
            if member is None:
                basename = Path(requested).name.lower()
                matches = [name for name in files if Path(name).name.lower() == basename]
                member = matches[0] if len(matches) == 1 else None
            if member is None:
                continue
            safe_name = Path(member).name
            destination = output_dir / f"{index:04d}-{safe_name}"
            destination.write_bytes(archive.read(member))
            raw["storage_path"] = str(destination)
            raw["source_storage_key"] = f"local://derived/{destination.name}"
            raw["file_name"] = destination.name
            raw["size_bytes"] = destination.stat().st_size
            raw["image_hash"] = raw.get("image_hash") or file_sha256(destination)
            raw["perceptual_hash"] = perceptual_hash(destination)
            raw["content_type"] = _content_type(destination)
    return image_assets


def _content_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".bmp": "image/bmp", ".gif": "image/gif", ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(suffix)
