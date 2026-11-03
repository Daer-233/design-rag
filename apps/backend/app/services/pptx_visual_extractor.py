"""Local fallback extraction of PPTX embedded design images for GPT Vision review.

LibreOffice can fail on unusually large or complex decks.  A PPTX is a ZIP package,
so we can still recover the raster visuals referenced by each slide without sending the
original presentation to an external service.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError


class PptxVisualExtractError(RuntimeError):
    """A PPTX could not yield any safe, slide-linked raster visual."""


@dataclass(frozen=True)
class ExtractedPptxVisual:
    slide_number: int
    image_index: int
    image_path: Path
    sha256: str
    width: int
    height: int
    size_bytes: int
    content_type: str = "image/jpeg"

    def metadata(self) -> dict[str, object]:
        return {
            "source_slide_number": self.slide_number,
            "source_image_index": self.image_index,
            "derivation_type": "pptx_embedded_image_extract",
            "derivative_sha256": self.sha256,
            "derivative_dimensions": {"width": self.width, "height": self.height},
            "derivative_content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "exif_removed": True,
        }


class PptxVisualExtractor:
    """Extract EXIF-free JPEG derivatives of raster images used by PPTX slides."""

    RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

    def __init__(self, *, max_dimension: int = 2048) -> None:
        self.max_dimension = max_dimension

    def extract(self, source_path: Path, *, output_root: Path, source_sha256: str) -> list[ExtractedPptxVisual]:
        source_path = source_path.resolve()
        if source_path.suffix.lower() != ".pptx":
            raise PptxVisualExtractError("PPTX embedded-image fallback only supports .pptx files")
        if not source_path.is_file():
            raise PptxVisualExtractError("Archived PPTX source is missing")
        if self._sha256(source_path) != source_sha256:
            raise PptxVisualExtractError("Archived PPTX SHA-256 no longer matches the batch manifest")

        root = (Path(output_root) / source_sha256 / "pptx-embedded-images").resolve()
        root.mkdir(parents=True, exist_ok=True)
        visuals: list[ExtractedPptxVisual] = []
        try:
            with zipfile.ZipFile(source_path) as archive:
                slide_media = self._slide_media_members(archive)
                for slide_number, media_members in slide_media.items():
                    for image_index, media_member in enumerate(media_members, start=1):
                        try:
                            data = archive.read(media_member)
                            visual = self._normalize_image(
                                data,
                                destination=root / f"slide-{slide_number:04d}-image-{image_index:03d}.jpg",
                                slide_number=slide_number,
                                image_index=image_index,
                            )
                        except (KeyError, OSError, UnidentifiedImageError, ValueError):
                            # Unsupported vector drawings, malformed assets, and tiny non-image
                            # decorations are intentionally omitted rather than sent to GPT Vision.
                            continue
                        visuals.append(visual)
        except zipfile.BadZipFile as error:
            raise PptxVisualExtractError("Archived PPTX is not a readable ZIP package") from error

        if not visuals:
            raise PptxVisualExtractError("PPTX contains no readable raster images linked from slides")
        return visuals

    def _slide_media_members(self, archive: zipfile.ZipFile) -> dict[int, list[str]]:
        members = set(archive.namelist())
        slides: dict[int, list[str]] = {}
        for rels_name in sorted(name for name in members if name.startswith("ppt/slides/_rels/slide") and name.endswith(".xml.rels")):
            stem = Path(rels_name).stem.replace(".xml", "")
            number_text = stem.removeprefix("slide")
            if not number_text.isdecimal():
                continue
            slide_number = int(number_text)
            root = ElementTree.fromstring(archive.read(rels_name))
            media_members: list[str] = []
            for relation in root.findall(f"{{{self.RELATIONSHIP_NS}}}Relationship"):
                target = relation.attrib.get("Target", "")
                if not target.startswith("../media/"):
                    continue
                member = "ppt/media/" + target.removeprefix("../media/")
                if member in members and member not in media_members:
                    media_members.append(member)
            if media_members:
                slides[slide_number] = media_members
        return slides

    def _normalize_image(self, data: bytes, *, destination: Path, slide_number: int, image_index: int) -> ExtractedPptxVisual:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            image = opened.convert("RGB")
            image.thumbnail((self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS)
            width, height = image.size
            image.save(destination, format="JPEG", quality=90, optimize=True, progressive=True, exif=b"")
        return ExtractedPptxVisual(
            slide_number=slide_number,
            image_index=image_index,
            image_path=destination.resolve(),
            sha256=self._sha256(destination),
            width=width,
            height=height,
            size_bytes=destination.stat().st_size,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
