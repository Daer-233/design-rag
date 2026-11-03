"""Download the BGE-M3 artifact from ModelScope into the configured local path.

This script intentionally uses ModelScope's snapshot_download API and never uses
Hugging Face as a model source. It records the source revision and SHA-256 hashes
of the files used by the CPU PyTorch inference path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from modelscope import snapshot_download

from embedding_app.core.config import settings


ARTIFACT_FILES = (
    "config.json",
    "pytorch_model.bin",
    "sentencepiece.bpe.model",
    "tokenizer.json",
    "sparse_linear.pt",
    "colbert_linear.pt",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    target = settings.model_dir
    target.parent.mkdir(parents=True, exist_ok=True)
    revision = settings.model_revision or "master"

    downloaded_path = snapshot_download(
        model_id=settings.model_id,
        revision=revision,
        local_dir=str(target),
    )

    artifact_files: dict[str, dict[str, int | str]] = {}
    for relative_name in ARTIFACT_FILES:
        path = target / relative_name
        if path.is_file():
            artifact_files[relative_name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }

    model_weights_sha256 = artifact_files.get("pytorch_model.bin", {}).get("sha256")
    manifest = {
        "provider": "modelscope",
        "model_id": settings.model_id,
        "source_revision": revision,
        "local_dir": str(Path(downloaded_path).resolve()),
        "pytorch_weights_sha256": model_weights_sha256,
        "artifact_files": artifact_files,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
    }
    (target / "design-rag-model-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
