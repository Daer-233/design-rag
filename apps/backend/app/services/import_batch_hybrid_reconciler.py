"""Second stage for the page pipeline: GPT sees MinerU-extracted images only."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from app.core.config import settings
from app.repositories.import_batch_repository import ImportBatchRepository
from app.services.ingestion_job_service import IngestionJobService
from app.services.visual_analysis_service import VisualAnalysisService

logger = logging.getLogger(__name__)


class ImportBatchHybridReconciler:
    """Materialize one GPT image job per MinerU image asset, idempotently."""

    def __init__(self, repository: ImportBatchRepository, ingestion_jobs: IngestionJobService,
                 visual_analysis: VisualAnalysisService) -> None:
        self.repository = repository
        self.ingestion_jobs = ingestion_jobs
        self.visual_analysis = visual_analysis

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected hybrid import reconciliation failure")
            await asyncio.sleep(settings.ingestion_reconcile_interval_seconds)

    def run_once(self) -> int:
        created = 0
        batches = self.repository.list_batches(limit=200, offset=0)["items"]
        for batch in batches:
            tasks = self.repository.list_tasks(str(batch["id"]))
            items = {str(item["id"]): item for item in self.repository.list_items(str(batch["id"]))}
            for parent in tasks:
                details = dict(parent.get("details") or {})
                if parent["task_kind"] != "mineru" or details.get("pipeline") != "mineru_then_gpt":
                    continue
                if parent["status"] not in {"succeeded", "processing", "queued"}:
                    continue
                job = self.ingestion_jobs.get(str(parent["ingestion_job_id"]))
                if job.get("status") != "succeeded" or not job.get("document_version_id"):
                    continue
                item = items.get(str(parent["item_id"]))
                if not item:
                    continue
                page_numbers = [int(value) for value in details.get("hybrid_source_page_numbers", [])]
                assets = self.ingestion_jobs.repository.list_mineru_image_assets(
                    str(parent["ingestion_job_id"]), source_pages=page_numbers or None
                )
                for asset in assets:
                    asset_id = str(asset["id"])
                    if self.repository.find_task_by_detail(batch_id=str(batch["id"]), key="source_image_asset_id", value=asset_id):
                        continue
                    source_path = self._approved_asset_path(asset.get("storage_path"))
                    if source_path is None:
                        logger.warning("Hybrid image asset %s is missing or outside approved storage", asset_id)
                        continue
                    # Do not trust a legacy image_hash here. It may be a perceptual
                    # hash rather than the file digest required by the ingestion API.
                    # Re-hashing also catches a stale/mutated derived file before it is
                    # sent to GPT and makes retries deterministic.
                    image_sha = self._file_sha256(source_path)
                    image_size = source_path.stat().st_size
                    image_content_type = str(asset.get("content_type") or "")
                    if not image_content_type or image_content_type == "None":
                        image_content_type = mimetypes.guess_type(source_path.name)[0] or "image/jpeg"
                    image_job = self.ingestion_jobs.create_from_archived_file(
                        source_path=source_path, source_sha256=image_sha, source_size_bytes=image_size,
                        source_content_type=image_content_type,
                        project_external_key=str(batch["project_external_key"]), project_name=str(batch["project_name"]),
                        source_relative_path=f"mineru-images/{Path(str(item['source_file_name'])).stem}/{source_path.name}",
                        document_metadata={
                            "source": "admin-batch-hybrid-import", "pipeline": "mineru_then_gpt",
                            "pipeline_stage": "gpt_image_analysis", "batch_id": str(batch["id"]),
                            "source_document_name": str(item["source_file_name"]),
                            "source_document_sha256": str(item["source_sha256"]),
                            "source_page_number": asset.get("page_number"), "source_image_asset_id": asset_id,
                            "bbox": asset.get("bbox") or {}, "evidence_type": "vision_inferred",
                            "source_mineru_ingestion_job_id": str(parent["ingestion_job_id"]),
                        },
                        processing_profile="vision",
                        document_external_key=f"{batch['project_external_key']}-{item['source_sha256'][:16]}-mineru-asset-{asset_id}",
                    )
                    child_details = {
                        "pipeline": "mineru_then_gpt", "pipeline_stage": "gpt_image_analysis",
                        "parent_mineru_task_id": str(parent["id"]), "parent_mineru_ingestion_job_id": str(parent["ingestion_job_id"]),
                        "source_image_asset_id": asset_id, "source_document_name": str(item["source_file_name"]),
                        "source_document_sha256": str(item["source_sha256"]), "source_page_number": asset.get("page_number"),
                        "bbox": asset.get("bbox") or {}, "evidence_type": "vision_inferred",
                        "file_name": source_path.name, "adjacent_text": asset.get("adjacent_text") or "",
                        "caption_text": asset.get("caption_text") or "",
                    }
                    child = self.repository.add_task(
                        batch_id=str(batch["id"]), item_id=str(item["id"]), task_kind="vision",
                        ingestion_job_id=str(image_job["id"]), source_ingestion_job_id=str(parent["ingestion_job_id"]),
                        file_name=source_path.name, file_type=source_path.suffix.lstrip(".") or "jpg",
                        source_sha256=image_sha, source_size_bytes=image_size, page_start=int(asset["page_number"]) if asset.get("page_number") else None,
                        page_end=int(asset["page_number"]) if asset.get("page_number") else None,
                        external_target="GPT Vision", details=child_details,
                    )
                    created += 1
                    if bool(batch.get("vision_approved")):
                        self._queue_child(str(batch["id"]), child, image_job)
                self.repository.update_task(str(parent["id"]), status="succeeded",
                    details={**details, "hybrid_vision_created": True, "hybrid_image_count": len(assets)})
                if not assets or bool(batch.get("vision_approved")):
                    self.repository.set_status(str(batch["id"]), status="queued" if assets else "processing")
                else:
                    self.repository.set_status(str(batch["id"]), status="awaiting_approval")
        return created

    @staticmethod
    def _approved_asset_path(raw_path: object) -> Path | None:
        """Resolve a materialized MinerU image without allowing arbitrary paths.

        The repository currently returns local paths. Keep the check here as a
        second line of defence because these paths originate in database metadata;
        object-storage migration can later replace this with a controlled cache
        download without changing the reconciliation contract.
        """
        if not raw_path:
            return None
        candidate = Path(str(raw_path)).resolve()
        allowed_roots = [
            (settings.data_root / "documents" / "derived").resolve(),
            (settings.data_root / "documents" / "import-batches").resolve(),
        ]
        for root in allowed_roots:
            try:
                candidate.relative_to(root)
                return candidate if candidate.is_file() else None
            except ValueError:
                continue
        logger.warning("Ignoring MinerU image outside approved storage: %s", candidate)
        return None

    def _queue_child(self, batch_id: str, child: dict[str, object], image_job: dict[str, object]) -> None:
        try:
            result = self.visual_analysis.queue_batch([str(image_job["id"])])[0]
            visual_job = result.get("job") if isinstance(result, dict) else None
            if isinstance(visual_job, dict) and visual_job.get("id"):
                self.repository.update_task(str(child["id"]), status="queued", visual_analysis_job_id=str(visual_job["id"]))
            elif result.get("outcome") in {"already_queued", "queued"}:
                self.repository.update_task(str(child["id"]), status="queued")
        except Exception as error:
            self.repository.update_task(str(child["id"]), status="failed", details={**dict(child.get("details") or {}), "queue_error": str(error)})
            logger.exception("Could not queue hybrid GPT image task for batch %s", batch_id)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): digest.update(chunk)
        return digest.hexdigest()
