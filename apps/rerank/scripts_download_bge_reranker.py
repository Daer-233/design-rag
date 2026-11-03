"""Download the BGE reranker artifact from ModelScope only and record its fingerprint."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from modelscope import snapshot_download

from rerank_app.core.config import settings


ARTIFACT_FILES = ("config.json", "model.safetensors", "sentencepiece.bpe.model", "tokenizer.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    settings.model_dir.parent.mkdir(parents=True, exist_ok=True)
    path = snapshot_download(
        model_id=settings.model_id,
        revision=settings.model_revision,
        local_dir=str(settings.model_dir),
    )
    artifact_files: dict[str, dict[str, int | str]] = {}
    for file_name in ARTIFACT_FILES:
        artifact = settings.model_dir / file_name
        if artifact.is_file():
            artifact_files[file_name] = {"bytes": artifact.stat().st_size, "sha256": sha256_file(artifact)}
    manifest = {
        "provider": "modelscope",
        "model_id": settings.model_id,
        "source_revision": settings.model_revision,
        "local_dir": str(Path(path).resolve()),
        "model_weights_sha256": artifact_files.get("model.safetensors", {}).get("sha256"),
        "artifact_files": artifact_files,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
    }
    (settings.model_dir / "design-rag-model-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
