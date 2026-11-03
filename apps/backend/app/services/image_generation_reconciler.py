from __future__ import annotations

import asyncio
import json
import logging
import shlex
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.repositories.harness_repository import HarnessRepository

logger = logging.getLogger(__name__)


class ImageGenerationReconciler:
    """Runs confirmed jobs through an explicitly configured local browser adapter."""

    def __init__(self, repository: HarnessRepository) -> None:
        self.repository = repository

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Image generation queue failure")
            await asyncio.sleep(settings.image_generation_poll_interval_seconds)

    def run_once(self) -> None:
        for job_id in self.repository.list_confirmed_generation_jobs():
            job = self.repository.claim_generation_job(job_id)
            if not job:
                continue
            command = settings.image_generation_adapter_command
            argv = shlex.split(command) if command else [sys.executable, "-m", "app.services.chatgpt_image_adapter"]
            try:
                result = subprocess.run(
                    argv,
                    input=json.dumps({
                        "job_id": str(job["id"]),
                        "project_id": str(job["project_id"]),
                        "prompt": job["prompt"],
                        "reference_asset_ids": job["reference_asset_ids"],
                        "citation_ids": job["citation_ids"],
                    }, ensure_ascii=False),
                    text=True,
                    capture_output=True,
                    timeout=1800,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or f"adapter exited with {result.returncode}")
                payload = json.loads(result.stdout)
                if not payload.get("output_path"):
                    raise RuntimeError("图片生成适配器没有返回 output_path")
                path = Path(str(payload["output_path"])).resolve()
                root = settings.data_root.resolve()
                path.relative_to(root)
                if not path.is_file():
                    raise RuntimeError("生成图片文件不存在")
                digest = sha256(path.read_bytes()).hexdigest()
                with Image.open(path) as image:
                    image.verify()
                    width, height = image.size
                    content_type = Image.MIME.get(image.format, "image/png")
                payload.update({"output_path": str(path), "sha256": digest, "size_bytes": path.stat().st_size, "width": width, "height": height, "content_type": content_type, "asset_type": "ai_concept_image"})
                self.repository.add_generation_output(job_id, job, payload)
                self.repository.finish_generation_job(job_id, "succeeded")
            except Exception as error:
                self.repository.finish_generation_job(job_id, "failed", str(error)[:1200])
                logger.exception("Image generation job %s failed", job_id)
