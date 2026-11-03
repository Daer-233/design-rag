"""Create safe local derivative images before external vision analysis."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


VISION_DERIVATION_TYPE = "vision_analysis_derivative"
VISION_PREPROCESSOR_VERSION = "vision-image-preprocessor-v1"


class VisionImagePreprocessError(RuntimeError):
    """The archived image cannot be safely normalized for external analysis."""


@dataclass(frozen=True)
class VisionImageDerivative:
    """A local, EXIF-free image that is the only image sent to the vision gateway."""

    source_path: Path
    derivative_path: Path
    source_sha256: str
    derivative_sha256: str
    source_width: int
    source_height: int
    derivative_width: int
    derivative_height: int
    content_type: str
    size_bytes: int
    orientation_corrected: bool
    max_dimension: int
    derivation_type: str = VISION_DERIVATION_TYPE
    generator_version: str = VISION_PREPROCESSOR_VERSION

    def request_metadata(self) -> dict[str, object]:
        return {
            "derivation_type": self.derivation_type,
            "generator_version": self.generator_version,
            "source_sha256": self.source_sha256,
            "derivative_sha256": self.derivative_sha256,
            "derivative_content_type": self.content_type,
            "source_dimensions": {"width": self.source_width, "height": self.source_height},
            "derivative_dimensions": {"width": self.derivative_width, "height": self.derivative_height},
            "orientation_corrected": self.orientation_corrected,
            "exif_removed": True,
            "max_dimension": self.max_dimension,
        }


class VisionImagePreprocessor:
    """Normalize visual inputs locally, deterministically, and without retaining EXIF."""

    def __init__(self, derivative_root: Path, *, max_dimension: int = 2048) -> None:
        if max_dimension < 256:
            raise ValueError("max_dimension must be at least 256 pixels")
        self.derivative_root = derivative_root
        self.max_dimension = max_dimension

    def prepare(self, source_path: Path, *, source_sha256: str) -> VisionImageDerivative:
        if not source_path.is_file():
            raise VisionImagePreprocessError("Archived source image is missing")
        actual_source_sha256 = self._sha256(source_path)
        if actual_source_sha256 != source_sha256:
            raise VisionImagePreprocessError("Archived source image SHA-256 no longer matches the queued job")

        try:
            with Image.open(source_path) as opened:
                opened.load()
                source_width, source_height = opened.size
                orientation = opened.getexif().get(274, 1)
                normalized = ImageOps.exif_transpose(opened)
                normalized.load()
                normalized = self._as_jpeg_image(normalized)
        except (OSError, ValueError) as error:
            raise VisionImagePreprocessError(f"Unable to normalize archived image: {type(error).__name__}") from error

        normalized.thumbnail((self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS)
        derivative_width, derivative_height = normalized.size
        target = self.derivative_root / source_sha256[:2] / f"{source_sha256}-vision-{self.max_dimension}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            temporary = target.with_suffix(".tmp.jpg")
            try:
                normalized.save(temporary, format="JPEG", quality=90, optimize=True, progressive=True)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink(missing_ok=True)

        return VisionImageDerivative(
            source_path=source_path,
            derivative_path=target,
            source_sha256=source_sha256,
            derivative_sha256=self._sha256(target),
            source_width=source_width,
            source_height=source_height,
            derivative_width=derivative_width,
            derivative_height=derivative_height,
            content_type="image/jpeg",
            size_bytes=target.stat().st_size,
            orientation_corrected=orientation not in (1, None),
            max_dimension=self.max_dimension,
        )

    @staticmethod
    def _as_jpeg_image(image: Image.Image) -> Image.Image:
        if image.mode == "RGB":
            return image.copy()
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
