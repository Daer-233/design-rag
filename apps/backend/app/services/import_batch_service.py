"""Batch archive, local preprocessing, explicit approval, and durable queues."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.repositories.import_batch_repository import ImportBatchRepository
from app.services.document_page_renderer import DocumentPageRenderError, DocumentPageRenderer
from app.services.ingestion_job_service import IngestionJobError, IngestionJobService
from app.services.pptx_visual_extractor import PptxVisualExtractError, PptxVisualExtractor
from app.services.vision_image_preprocessor import VisionImagePreprocessError, VisionImagePreprocessor
from app.services.visual_analysis_service import VisualAnalysisJobError, VisualAnalysisService


class ImportBatchError(RuntimeError):
    pass


class ImportBatchService:
    """Archives locally first; background workers only see explicitly approved tasks."""

    IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
    PAGE_RENDER_EXTENSIONS = frozenset({"pdf", "ppt", "pptx"})

    def __init__(self, repository: ImportBatchRepository, ingestion_job_service: IngestionJobService,
                 visual_analysis_service: VisualAnalysisService,
                 image_preprocessor: VisionImagePreprocessor | None = None,
                 document_page_renderer: DocumentPageRenderer | None = None,
                 pptx_visual_extractor: PptxVisualExtractor | None = None) -> None:
        self.repository = repository
        self.ingestion_job_service = ingestion_job_service
        self.visual_analysis_service = visual_analysis_service
        self.image_preprocessor = image_preprocessor or VisionImagePreprocessor(
            settings.vision_analysis_derivative_root, max_dimension=settings.vision_analysis_max_dimension
        )
        self.document_page_renderer = document_page_renderer
        self.pptx_visual_extractor = pptx_visual_extractor or PptxVisualExtractor(
            max_dimension=settings.vision_analysis_max_dimension
        )

    def create(self, *, project_external_key: str, project_name: str, force_reprocess: bool = False) -> dict[str, object]:
        return self._public_batch(self.repository.create_batch(
            project_external_key=self._required(project_external_key, "project_external_key"),
            project_name=self._required(project_name, "project_name"),
            force_reprocess=force_reprocess,
        ))

    async def upload_file(self, *, batch_id: str, file: UploadFile,
                          source_relative_path: str | None) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        if batch["status"] != "uploading":
            raise ImportBatchError("Only an uploading batch can accept more files")
        relative_path = self._safe_relative_path(source_relative_path or file.filename or "upload")
        if self.repository.get_item_by_path(batch_id=batch_id, source_relative_path=relative_path):
            raise ImportBatchError("This relative file path already exists in the batch")
        file_name = Path(relative_path).name
        extension = Path(file_name).suffix.lower().lstrip(".")
        if extension not in settings.ingestion_allowed_extensions:
            raise ImportBatchError(f"Unsupported file type '.{extension or 'unknown'}'")
        root = (settings.data_root / "documents" / "import-batches" / batch_id / "sources").resolve()
        destination = (root / Path(relative_path)).resolve()
        try:
            destination.relative_to(root)
        except ValueError as error:
            raise ImportBatchError("Invalid source_relative_path") from error
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest, total = hashlib.sha256(), 0
        try:
            with destination.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.ingestion_max_upload_bytes:
                        target.close(); destination.unlink(missing_ok=True)
                        raise ImportBatchError(f"Upload exceeds configured limit of {settings.ingestion_max_upload_bytes} bytes")
                    digest.update(chunk); target.write(chunk)
        finally:
            await file.close()
        source_sha256 = digest.hexdigest()
        existing = self.repository.find_existing_ingestion_job(
            project_external_key=str(batch["project_external_key"]), source_sha256=source_sha256
        )
        if existing and not bool(batch.get("force_reprocess")):
            return self._public_item(self.repository.add_item(
                batch_id=batch_id, ingestion_job_id=None, existing_ingestion_job_id=str(existing["id"]),
                source_relative_path=relative_path, source_file_name=file_name, source_file_type=extension,
                source_sha256=source_sha256, source_size_bytes=total, source_file_path=str(destination),
                outcome="duplicate_skipped", processing_plan={"duplicate_of_ingestion_job_id": str(existing["id"]),
                "existing_status": str(existing["status"])},
            ))
        try:
            job = self.ingestion_job_service.create_from_archived_file(
                source_path=destination, source_sha256=source_sha256, source_size_bytes=total,
                source_content_type=file.content_type or mimetypes.guess_type(file_name)[0],
                project_external_key=str(batch["project_external_key"]), project_name=str(batch["project_name"]),
                source_relative_path=relative_path,
                document_metadata={
                    "source": "admin-batch-import", "source_relative_path": relative_path, "batch_id": batch_id,
                    **({"reimport_of_ingestion_job_id": str(existing["id"]), "reimport_reason": "admin_batch_force_reprocess"}
                       if existing and bool(batch.get("force_reprocess")) else {}),
                },
                document_external_key=(
                    f"{batch['project_external_key']}-{self._safe_document_key(relative_path)}-{source_sha256[:12]}-reimport-{batch_id.replace('-', '')[:8]}"
                    if existing and bool(batch.get("force_reprocess")) else None
                ),
            )
        except IngestionJobError as error:
            return self._public_item(self.repository.add_item(
                batch_id=batch_id, ingestion_job_id=None, existing_ingestion_job_id=None,
                source_relative_path=relative_path, source_file_name=file_name, source_file_type=extension,
                source_sha256=source_sha256, source_size_bytes=total, source_file_path=str(destination),
                outcome="failed", error_code="ARCHIVE_JOB_CREATE_FAILED", error_message=str(error),
            ))
        return self._public_item(self.repository.add_item(
            batch_id=batch_id, ingestion_job_id=str(job["id"]), existing_ingestion_job_id=None,
            source_relative_path=relative_path, source_file_name=file_name, source_file_type=extension,
            source_sha256=source_sha256, source_size_bytes=total, source_file_path=str(destination), outcome="archived",
            processing_plan={
                "processing_route": str(job["processing_route"]), "route_reason": str(job["route_reason"]),
                **({"reprocess_of_ingestion_job_id": str(existing["id"]), "reprocess_reason": "admin_batch_force_reprocess"}
                   if existing and bool(batch.get("force_reprocess")) else {}),
            },
        ))

    def prepare(self, batch_id: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        # A browser retry after the local pages have already been rendered must never
        # create a second set of review rows or re-render the document.
        if batch["status"] in {"awaiting_page_review", "awaiting_approval"}:
            return self.get(batch_id)
        if batch["status"] != "uploading":
            raise ImportBatchError(f"Batch cannot be prepared from status '{batch['status']}'")
        items = self.repository.list_items(batch_id)
        if not items:
            raise ImportBatchError("Upload at least one file before preparing the manifest")
        manifest_items: list[dict[str, object]] = []
        for item in items:
            plan = dict(item.get("processing_plan") or {})
            manifest_item = {
                "id": str(item["id"]), "source_relative_path": str(item["source_relative_path"]),
                "file_name": str(item["source_file_name"]), "file_type": str(item["source_file_type"]),
                "sha256": str(item["source_sha256"]), "size_bytes": int(item["source_size_bytes"]),
                "outcome": str(item["outcome"]), "processing_plan": plan,
            }
            if item["outcome"] != "archived" or not item.get("ingestion_job_id"):
                manifest_items.append(manifest_item)
                continue
            job = self.ingestion_job_service.get(str(item["ingestion_job_id"]))
            extension = str(item["source_file_type"]).lower()
            try:
                if extension in self.IMAGE_EXTENSIONS:
                    self._prepare_image_vision_task(batch_id, item, job, plan)
                elif extension == "pdf" and self.document_page_renderer is not None:
                    # PDF sources are always reviewed locally page by page before any external task exists.
                    self._prepare_pdf_page_reviews(batch_id, item, job, plan)
                else:
                    if int(job["source_size_bytes"]) <= settings.mineru_precision_max_file_bytes:
                        self._prepare_mineru_task(batch_id, item, job)
                    else:
                        plan["mineru"] = {
                            "status": "not_planned",
                            "reason": "Original source exceeds the configured MinerU Precision API size limit.",
                            "source_size_bytes": int(job["source_size_bytes"]),
                            "mineru_max_bytes": settings.mineru_precision_max_file_bytes,
                        }
                    if (
                        settings.document_page_vision_enabled
                        and extension in self.PAGE_RENDER_EXTENSIONS
                        and self.document_page_renderer is not None
                    ):
                        self._prepare_document_page_tasks(batch_id, batch, item, job, plan)
                self.repository.update_item(str(item["id"]), processing_plan=plan)
                manifest_item["processing_plan"] = plan
            except (DocumentPageRenderError, VisionImagePreprocessError, IngestionJobError, ValueError, OSError) as error:
                error_code = "DOCUMENT_PAGE_RENDER_FAILED" if extension in self.PAGE_RENDER_EXTENSIONS else "VISION_PREPROCESS_FAILED"
                self.repository.update_item(
                    str(item["id"]), outcome="failed", error_code=error_code, error_message=str(error),
                    processing_plan=plan,
                )
                manifest_item.update({
                    "outcome": "failed", "error_code": error_code, "error_message": str(error),
                    "processing_plan": plan,
                })
            manifest_items.append(manifest_item)
        tasks = self.repository.list_tasks(batch_id)
        page_reviews = self.repository.list_page_reviews(batch_id)
        manifest = self._manifest(batch, manifest_items, tasks, page_reviews=page_reviews)
        self.repository.set_prepared_manifest(
            batch_id=batch_id, manifest=manifest, manifest_sha256=self._manifest_sha256(manifest),
            status="awaiting_page_review" if page_reviews else "awaiting_approval",
        )
        return self.get(batch_id)

    def retry_page_processing(self, batch_id: str) -> dict[str, object]:
        """Retry only local PDF/PPT page derivation failures without re-uploading sources."""
        batch = self._require_batch(batch_id)
        if batch["status"] not in {"awaiting_page_review", "awaiting_approval", "failed", "partially_succeeded"}:
            raise ImportBatchError("Page processing can only be retried after a prepared or completed batch")
        retryable = [
            item for item in self.repository.list_items(batch_id)
            if item.get("error_code") == "DOCUMENT_PAGE_RENDER_FAILED"
            and str(item.get("source_file_type", "")).lower() in self.PAGE_RENDER_EXTENSIONS
            and item.get("ingestion_job_id")
        ]
        if not retryable:
            raise ImportBatchError("This batch has no retryable document page-render failures")
        for item in retryable:
            self.repository.update_item(
                str(item["id"]), outcome="archived", processing_plan=dict(item.get("processing_plan") or {}),
                clear_error=True,
            )
        self.repository.set_status(batch_id, status="uploading")
        return self.prepare(batch_id)
    def _prepare_image_vision_task(
        self, batch_id: str, item: dict[str, object], job: dict[str, object], plan: dict[str, object]
    ) -> None:
        derivative = self.image_preprocessor.prepare(
            Path(str(job["source_file_path"])), source_sha256=str(item["source_sha256"])
        )
        asset = self.ingestion_job_service.repository.ensure_visual_analysis_derivative_asset(
            ingestion_job_id=str(job["id"]), source_sha256=derivative.source_sha256,
            derivative_path=str(derivative.derivative_path), derivative_sha256=derivative.derivative_sha256,
            content_type=derivative.content_type, size_bytes=derivative.size_bytes,
            derivation_type=derivative.derivation_type, generator_version=derivative.generator_version,
            metadata=derivative.request_metadata(),
        )
        details = {
            **derivative.request_metadata(), "derivative_asset_id": str(asset["id"]),
            "derivative_file_name": derivative.derivative_path.name, "size_bytes": derivative.size_bytes,
        }
        self.repository.add_task(
            batch_id=batch_id, item_id=str(item["id"]), task_kind="vision",
            ingestion_job_id=str(job["id"]), source_ingestion_job_id=str(job["id"]),
            file_name=derivative.derivative_path.name, file_type="jpg",
            source_sha256=derivative.derivative_sha256, source_size_bytes=derivative.size_bytes,
            external_target="GPT Vision", details=details,
        )
        plan["vision_derivative"] = details

    def _prepare_mineru_task(
        self, batch_id: str, item: dict[str, object], job: dict[str, object]
    ) -> None:
        self.repository.add_task(
            batch_id=batch_id, item_id=str(item["id"]), task_kind="mineru",
            ingestion_job_id=str(job["id"]), source_ingestion_job_id=str(job["id"]),
            file_name=str(job["source_file_name"]), file_type=str(job["file_type"]),
            source_sha256=str(job["source_sha256"]), source_size_bytes=int(job["source_size_bytes"]),
            external_target="MinerU Precision API",
            details={"processing_route": str(job["processing_route"]), "route_reason": str(job["route_reason"])},
        )

    def _prepare_pdf_page_reviews(self, batch_id: str, item: dict[str, object], job: dict[str, object],
                                  plan: dict[str, object]) -> None:
        assert self.document_page_renderer is not None
        output_root = settings.data_root / "documents" / "import-batches" / batch_id / "derived-pages"
        rendered = self.document_page_renderer.render(
            Path(str(job["source_file_path"])), output_root=output_root, source_sha256=str(item["source_sha256"]),
        )
        pages: list[dict[str, object]] = []
        for page in rendered.pages:
            review = self.repository.add_page_review(
                batch_id=batch_id, item_id=str(item["id"]), source_page_number=page.page_number,
                preview_file_path=str(page.image_path), preview_sha256=page.sha256, preview_content_type=page.content_type,
                preview_size_bytes=page.size_bytes, preview_width=page.width, preview_height=page.height,
                selected_processor="mineru",
            )
            pages.append({
                "id": str(review["id"]), "source_page_number": page.page_number,
                "preview_sha256": page.sha256, "size_bytes": page.size_bytes,
                "preview_dimensions": {"width": page.width, "height": page.height}, "selected_processor": "mineru",
            })
        plan["pdf_page_review"] = {
            "enabled": True, "status": "awaiting_page_review", "source_document_name": str(item["source_file_name"]),
            "page_count": rendered.page_count, "render_dpi": self.document_page_renderer.dpi,
            "rendered_pdf_file_name": rendered.pdf_path.name, "converted_to_pdf": rendered.converted_to_pdf,
            "pages": pages, "external_export_requires_explicit_approval": True,
        }

    def list_page_reviews(self, batch_id: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        return {"batch_id": batch_id, "status": str(batch["status"]),
                "items": [self._public_page_review(row) for row in self.repository.list_page_reviews(batch_id)]}

    def get_page_review(self, batch_id: str, page_review_id: str, *, include_local_path: bool = False) -> dict[str, object]:
        self._require_batch(batch_id)
        review = self.repository.get_page_review(batch_id=batch_id, page_review_id=page_review_id)
        if not review:
            raise ImportBatchError("Page review not found")
        return self._public_page_review(review, include_local_path=include_local_path)

    def update_page_review(self, batch_id: str, page_review_id: str, selected_processor: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        if batch["status"] != "awaiting_page_review":
            raise ImportBatchError("Page processor can only be changed while the batch is awaiting page review")
        if selected_processor not in {"mineru", "gpt_vision", "mineru_then_gpt"}:
            raise ImportBatchError("Unsupported page processor")
        if not self.repository.get_page_review(batch_id=batch_id, page_review_id=page_review_id):
            raise ImportBatchError("Page review not found")
        self.repository.update_page_reviews(batch_id=batch_id, page_review_ids=[page_review_id], selected_processor=selected_processor)
        return self.get(batch_id)

    def bulk_update_page_reviews(self, batch_id: str, page_review_ids: list[str], selected_processor: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        if batch["status"] != "awaiting_page_review":
            raise ImportBatchError("Page processor can only be changed while the batch is awaiting page review")
        if selected_processor not in {"mineru", "gpt_vision", "mineru_then_gpt"}:
            raise ImportBatchError("Unsupported page processor")
        reviews = {str(row["id"]) for row in self.repository.list_page_reviews(batch_id)}
        if not page_review_ids or any(value not in reviews for value in page_review_ids):
            raise ImportBatchError("One or more page reviews do not belong to this batch")
        self.repository.update_page_reviews(batch_id=batch_id, page_review_ids=page_review_ids, selected_processor=selected_processor)
        return self.get(batch_id)

    def confirm_page_reviews(self, batch_id: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        if batch["status"] != "awaiting_page_review":
            raise ImportBatchError("Only a batch awaiting page review can be confirmed")
        items = {str(item["id"]): item for item in self.repository.list_items(batch_id)}
        reviews = self.repository.list_page_reviews(batch_id)
        if not reviews:
            raise ImportBatchError("This batch has no PDF pages to confirm")
        by_item: dict[str, list[dict[str, object]]] = defaultdict(list)
        for review in reviews:
            by_item[str(review["item_id"])].append(review)
        for item_id, item_reviews in by_item.items():
            item = items.get(item_id)
            if not item or not item.get("ingestion_job_id"):
                continue
            source_job = self.ingestion_job_service.get(str(item["ingestion_job_id"]))
            self._create_confirmed_pdf_page_tasks(batch_id, item, source_job, item_reviews)
        self.repository.confirm_page_reviews(batch_id=batch_id)
        tasks = self.repository.list_tasks(batch_id)
        manifest = self._manifest(batch, self.repository.list_items(batch_id), tasks, page_reviews=self.repository.list_page_reviews(batch_id))
        self.repository.set_prepared_manifest(batch_id=batch_id, manifest=manifest, manifest_sha256=self._manifest_sha256(manifest))
        return self.get(batch_id)

    def reopen_page_reviews(self, batch_id: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        if batch["status"] != "awaiting_approval":
            raise ImportBatchError("Only an unqueued confirmed batch can be reopened")
        if any(task["status"] != "awaiting_approval" for task in self.repository.list_tasks(batch_id)):
            raise ImportBatchError("A page review cannot be reopened after tasks have entered a queue")
        self.repository.delete_awaiting_approval_tasks(batch_id=batch_id)
        self.repository.set_status(batch_id, status="awaiting_page_review")
        return self.get(batch_id)

    def _create_confirmed_pdf_page_tasks(self, batch_id: str, item: dict[str, object], source_job: dict[str, object],
                                         reviews: list[dict[str, object]]) -> None:
        mineru_pages = sorted((row for row in reviews if row["selected_processor"] in {"mineru", "mineru_then_gpt"}), key=lambda row: int(row["source_page_number"]))
        hybrid_page_numbers = {int(row["source_page_number"]) for row in reviews if row["selected_processor"] == "mineru_then_gpt"}
        vision_pages = sorted((row for row in reviews if row["selected_processor"] == "gpt_vision"), key=lambda row: int(row["source_page_number"]))
        source_path = Path(str(source_job["source_file_path"]))
        for page_group in self._split_mineru_page_groups(source_path, mineru_pages, batch_id=batch_id, item=item):
            page_numbers = [int(row["source_page_number"]) for row in page_group["reviews"]]
            derivative_path = page_group["path"]
            sha256 = self._file_sha256(derivative_path)
            derived_job = self.ingestion_job_service.create_from_archived_file(
                source_path=derivative_path, source_sha256=sha256, source_size_bytes=derivative_path.stat().st_size,
                source_content_type="application/pdf", project_external_key=str(source_job["project_external_key"]),
                project_name=str(source_job["project_name"]),
                source_relative_path=f"derived-mineru-pages/{Path(str(item['source_file_name'])).stem}/pages-{page_numbers[0]:04d}-{page_numbers[-1]:04d}.pdf",
                document_metadata={"source": "admin-batch-import-page-review", "batch_id": batch_id,
                                   "original_source_file_name": str(item["source_file_name"]),
                                   "original_source_sha256": str(item["source_sha256"]), "source_page_numbers": page_numbers},
                processing_profile="document",
                document_external_key=f"{source_job['project_external_key']}-{self._safe_document_key(str(item['source_relative_path']))}-pages-{page_numbers[0]}-{page_numbers[-1]}-{sha256[:12]}",
            )
            task = self.repository.add_task(
                batch_id=batch_id, item_id=str(item["id"]), task_kind="mineru", ingestion_job_id=str(derived_job["id"]),
                source_ingestion_job_id=str(source_job["id"]), file_name=derivative_path.name, file_type="pdf",
                source_sha256=sha256, source_size_bytes=derivative_path.stat().st_size, page_start=page_numbers[0],
                page_end=page_numbers[-1], external_target="MinerU Precision API",
                details={"source_document_name": str(item["source_file_name"]), "source_page_numbers": page_numbers,
                         "derivation_type": "pdf_page_range", "original_source_sha256": str(item["source_sha256"]),
                         "pipeline": "mineru_then_gpt" if hybrid_page_numbers.intersection(page_numbers) else "mineru",
                         "pipeline_stage": "mineru_extract", "post_mineru_action": "create_vision_jobs",
                         "hybrid_source_page_numbers": sorted(hybrid_page_numbers.intersection(page_numbers))},
            )
            for review in page_group["reviews"]:
                self.repository.link_page_review_task(page_review_id=str(review["id"]), processor="mineru", task_id=str(task["id"]))
        for review in vision_pages:
            preview_path = Path(str(review["preview_file_path"]))
            page_number = int(review["source_page_number"])
            page_job = self.ingestion_job_service.create_from_archived_file(
                source_path=preview_path, source_sha256=str(review["preview_sha256"]), source_size_bytes=int(review["preview_size_bytes"]),
                source_content_type=str(review["preview_content_type"]), project_external_key=str(source_job["project_external_key"]),
                project_name=str(source_job["project_name"]),
                source_relative_path=f"derived-pages/{Path(str(item['source_file_name'])).stem}/page-{page_number:04d}.jpg",
                document_metadata={"source": "admin-batch-import-page-review", "batch_id": batch_id,
                                   "source_document_name": str(item["source_file_name"]), "source_page_number": page_number,
                                   "source_sha256": str(item["source_sha256"]), "evidence_type": "vision_inferred"},
                processing_profile="vision",
                document_external_key=f"{source_job['project_external_key']}-{self._safe_document_key(str(item['source_relative_path']))}-page-{page_number}-{str(review['preview_sha256'])[:12]}",
            )
            task = self.repository.add_task(
                batch_id=batch_id, item_id=str(item["id"]), task_kind="vision", ingestion_job_id=str(page_job["id"]),
                source_ingestion_job_id=str(page_job["id"]), file_name=preview_path.name, file_type="jpg",
                source_sha256=str(review["preview_sha256"]), source_size_bytes=int(review["preview_size_bytes"]),
                page_start=page_number, page_end=page_number, external_target="GPT Vision",
                details={"source_document_name": str(item["source_file_name"]), "source_page_number": page_number,
                         "derivative_file_name": preview_path.name, "derivation_type": "pdf_page_render", "evidence_type": "vision_inferred"},
            )
            self.repository.link_page_review_task(page_review_id=str(review["id"]), processor="gpt_vision", task_id=str(task["id"]))

    def _split_mineru_page_groups(self, source_path: Path, reviews: list[dict[str, object]], *, batch_id: str,
                                  item: dict[str, object]) -> list[dict[str, object]]:
        consecutive: list[list[dict[str, object]]] = []
        for review in reviews:
            if not consecutive or int(review["source_page_number"]) != int(consecutive[-1][-1]["source_page_number"]) + 1:
                consecutive.append([review])
            else:
                consecutive[-1].append(review)
        output: list[dict[str, object]] = []
        for group in consecutive:
            output.extend(self._write_mineru_pdf_group(source_path, group, batch_id=batch_id, item=item))
        return output

    def _write_mineru_pdf_group(self, source_path: Path, reviews: list[dict[str, object]], *, batch_id: str,
                                item: dict[str, object]) -> list[dict[str, object]]:
        pages = [int(row["source_page_number"]) for row in reviews]
        root = settings.data_root / "documents" / "import-batches" / batch_id / "mineru-page-ranges" / str(item["id"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"pages-{pages[0]:04d}-{pages[-1]:04d}.pdf"
        writer, reader = PdfWriter(), PdfReader(str(source_path))
        for number in pages:
            writer.add_page(reader.pages[number - 1])
        with path.open("wb") as stream:
            writer.write(stream)
        if path.stat().st_size <= settings.mineru_precision_max_file_bytes or len(reviews) == 1:
            return [{"path": path, "reviews": reviews}]
        midpoint = len(reviews) // 2
        path.unlink(missing_ok=True)
        return self._write_mineru_pdf_group(source_path, reviews[:midpoint], batch_id=batch_id, item=item) + self._write_mineru_pdf_group(source_path, reviews[midpoint:], batch_id=batch_id, item=item)

    def _prepare_document_page_tasks(
        self,
        batch_id: str,
        batch: dict[str, object],
        item: dict[str, object],
        job: dict[str, object],
        plan: dict[str, object],
    ) -> None:
        assert self.document_page_renderer is not None
        output_root = settings.data_root / "documents" / "import-batches" / batch_id / "derived-pages"
        source_path = Path(str(job["source_file_path"]))
        try:
            rendered = self.document_page_renderer.render(
                source_path, output_root=output_root, source_sha256=str(item["source_sha256"]),
            )
        except DocumentPageRenderError as render_error:
            if source_path.suffix.lower() != ".pptx":
                raise
            try:
                extracted = self.pptx_visual_extractor.extract(
                    source_path, output_root=output_root, source_sha256=str(item["source_sha256"]),
                )
            except PptxVisualExtractError:
                raise render_error
            self._prepare_pptx_embedded_image_tasks(batch_id, batch, item, job, plan, extracted, render_error)
            return

        page_entries: list[dict[str, object]] = []
        for page in rendered.pages:
            relative_name = (
                f"derived-pages/{Path(str(item['source_file_name'])).stem}/"
                f"page-{page.page_number:04d}.jpg"
            )
            page_metadata = {
                "source": "admin-batch-import",
                "batch_id": batch_id,
                "source_kind": "page_render",
                "source_document_name": str(item["source_file_name"]),
                "source_page_number": page.page_number,
                "source_sha256": str(item["source_sha256"]),
                "rendered_page_sha256": page.sha256,
                "derivation_type": "pdf_page_render",
            }
            page_job = self.ingestion_job_service.create_from_archived_file(
                source_path=page.image_path, source_sha256=page.sha256, source_size_bytes=page.size_bytes,
                source_content_type=page.content_type,
                project_external_key=str(batch["project_external_key"]), project_name=str(batch["project_name"]),
                source_relative_path=relative_name, document_metadata=page_metadata, processing_profile="vision",
            )
            details = {
                **page.metadata(),
                "source_document_name": str(item["source_file_name"]),
                "source_document_sha256": str(item["source_sha256"]),
                "source_document_ingestion_job_id": str(job["id"]),
                "rendered_pdf_file_name": rendered.pdf_path.name,
                "converted_to_pdf": rendered.converted_to_pdf,
                "ingestion_job_id": str(page_job["id"]),
                "derivative_file_name": page.image_path.name,
                "evidence_type": "vision_inferred",
                "review_status": "pending",
            }
            self.repository.add_task(
                batch_id=batch_id, item_id=str(item["id"]), task_kind="vision",
                ingestion_job_id=str(page_job["id"]), source_ingestion_job_id=str(page_job["id"]),
                file_name=page.image_path.name, file_type="jpg", source_sha256=page.sha256,
                source_size_bytes=page.size_bytes, external_target="GPT Vision", details=details,
                page_start=page.page_number, page_end=page.page_number,
            )
            page_entries.append(details)
        plan["document_page_vision"] = {
            "enabled": True,
            "status": "awaiting_approval",
            "source_document_name": str(item["source_file_name"]),
            "source_type": rendered.source_type,
            "converted_to_pdf": rendered.converted_to_pdf,
            "rendered_pdf_file_name": rendered.pdf_path.name,
            "page_count": rendered.page_count,
            "vision_task_count": len(page_entries),
            "render_dpi": self.document_page_renderer.dpi,
            "pages": page_entries,
            "external_export_requires_explicit_approval": True,
        }

    def _prepare_pptx_embedded_image_tasks(
        self,
        batch_id: str,
        batch: dict[str, object],
        item: dict[str, object],
        job: dict[str, object],
        plan: dict[str, object],
        extracted: list[object],
        render_error: DocumentPageRenderError,
    ) -> None:
        """Use local PPTX raster assets if LibreOffice cannot render a complex deck."""
        image_entries: list[dict[str, object]] = []
        for visual in extracted:
            relative_name = (
                f"derived-pages/{Path(str(item['source_file_name'])).stem}/"
                f"slide-{visual.slide_number:04d}-image-{visual.image_index:03d}.jpg"
            )
            metadata = {
                "source": "admin-batch-import",
                "batch_id": batch_id,
                "source_kind": "pptx_embedded_image",
                "source_document_name": str(item["source_file_name"]),
                "source_slide_number": visual.slide_number,
                "source_image_index": visual.image_index,
                "source_sha256": str(item["source_sha256"]),
                "rendered_page_sha256": visual.sha256,
                "derivation_type": "pptx_embedded_image_extract",
            }
            visual_job = self.ingestion_job_service.create_from_archived_file(
                source_path=visual.image_path, source_sha256=visual.sha256, source_size_bytes=visual.size_bytes,
                source_content_type=visual.content_type,
                project_external_key=str(batch["project_external_key"]), project_name=str(batch["project_name"]),
                source_relative_path=relative_name, document_metadata=metadata, processing_profile="vision",
            )
            details = {
                **visual.metadata(),
                "source_document_name": str(item["source_file_name"]),
                "source_document_sha256": str(item["source_sha256"]),
                "source_document_ingestion_job_id": str(job["id"]),
                "ingestion_job_id": str(visual_job["id"]),
                "derivative_file_name": visual.image_path.name,
                "evidence_type": "vision_inferred",
                "review_status": "pending",
            }
            self.repository.add_task(
                batch_id=batch_id, item_id=str(item["id"]), task_kind="vision",
                ingestion_job_id=str(visual_job["id"]), source_ingestion_job_id=str(visual_job["id"]),
                file_name=visual.image_path.name, file_type="jpg", source_sha256=visual.sha256,
                source_size_bytes=visual.size_bytes, external_target="GPT Vision", details=details,
                page_start=visual.slide_number, page_end=visual.slide_number,
            )
            image_entries.append(details)
        plan["document_page_vision"] = {
            "enabled": True,
            "status": "awaiting_approval",
            "source_document_name": str(item["source_file_name"]),
            "source_type": "pptx",
            "fallback": "pptx_embedded_image_extract",
            "fallback_reason": str(render_error),
            "image_count": len(image_entries),
            "vision_task_count": len(image_entries),
            "images": image_entries,
            "external_export_requires_explicit_approval": True,
        }
    def approve(self, *, batch_id: str, manifest_sha256: str, approve_mineru: bool,
                approve_vision: bool, schedule_mode: str = "immediate",
                scheduled_for: datetime | None = None) -> dict[str, object]:
        """Record explicit export approval, then either queue now or persist a future schedule.

        Scheduling never discovers new work: only the task kinds selected in this exact
        manifest are marked scheduled.  The durable task state is the audit trail that
        lets a restarted local backend resume the same authorized export later.
        """
        batch = self._require_batch(batch_id)
        if batch["status"] != "awaiting_approval":
            raise ImportBatchError("Only a prepared batch can be approved")
        if manifest_sha256 != batch.get("manifest_sha256"):
            raise ImportBatchError("The manifest has changed. Refresh it and approve the exact displayed version.")
        if not approve_mineru and not approve_vision:
            raise ImportBatchError("Select at least one explicitly authorized external processor")
        # Older test repositories and one-time migration fixtures may not expose the
        # optional durable approval columns yet; the production repository does.
        set_approvals = getattr(self.repository, "set_processor_approvals", None)
        if set_approvals:
            set_approvals(batch_id=batch_id, approve_mineru=approve_mineru, approve_vision=approve_vision)
        if schedule_mode not in {"immediate", "nightly", "custom"}:
            raise ImportBatchError("Unsupported parsing schedule")

        eligible = [
            task for task in self.repository.list_tasks(batch_id)
            if task["status"] == "awaiting_approval"
            and ((task["task_kind"] == "mineru" and approve_mineru)
                 or (task["task_kind"] == "vision" and approve_vision))
        ]
        if not eligible:
            raise ImportBatchError("The selected processors have no tasks awaiting approval")

        if schedule_mode == "immediate":
            self._queue_approved_tasks(
                batch_id=batch_id, eligible_status="awaiting_approval", eligible_task_ids={str(task["id"]) for task in eligible}
            )
            return self.get(batch_id)

        run_at = self._resolve_scheduled_for(schedule_mode=schedule_mode, scheduled_for=scheduled_for)
        for task in eligible:
            self.repository.update_task(str(task["id"]), status="scheduled",
                                        details={**dict(task.get("details") or {}), "scheduled_for": run_at.isoformat()})
            self.repository.set_page_review_task_state(task_id=str(task["id"]), status="scheduled")
        self.repository.schedule_approved_batch(batch_id=batch_id, schedule_mode=schedule_mode, scheduled_for=run_at)
        return self.get(batch_id)

    def dispatch_due_schedules(self, *, limit: int = 20) -> int:
        """Move only already-approved, due import batches into the existing local queues."""
        claimed_batch_ids = self.repository.claim_due_scheduled_batches(limit=limit)
        for batch_id in claimed_batch_ids:
            try:
                self._queue_approved_tasks(batch_id=batch_id, eligible_status="scheduled")
            except Exception as error:  # keep the durable batch visible instead of silently losing a schedule
                self.repository.set_status(batch_id, status="failed", error_code="SCHEDULE_DISPATCH_FAILED", error_message=str(error))
        return len(claimed_batch_ids)

    def _queue_approved_tasks(self, *, batch_id: str, eligible_status: str,
                              eligible_task_ids: set[str] | None = None) -> tuple[int, list[str]]:
        """Queue task records which have already passed explicit approval.

        ``eligible_status`` prevents a scheduled dispatch from accidentally exporting
        an unrelated task that remains in ``awaiting_approval``.
        """
        queued, failures = 0, []
        for task in self.repository.list_tasks(batch_id):
            if task["status"] != eligible_status or (eligible_task_ids is not None and str(task["id"]) not in eligible_task_ids):
                continue
            details = dict(task.get("details") or {})
            if task["task_kind"] == "mineru":
                try:
                    result = self.ingestion_job_service.enqueue(str(task["ingestion_job_id"]))
                    if result.get("outcome") in {"queued", "already_queued"}:
                        self.repository.update_task(str(task["id"]), status="queued", details={**details, "queue_outcome": result.get("outcome")})
                        self.repository.set_page_review_task_state(task_id=str(task["id"]), status="queued")
                        queued += 1
                    else:
                        reason = str(result.get("reason") or "MinerU task was not queued")
                        self.repository.update_task(str(task["id"]), status="failed", details={**details, "queue_result": result})
                        self.repository.set_page_review_task_state(task_id=str(task["id"]), status="failed", error_code="QUEUE_FAILED", error_message=reason)
                        failures.append(reason)
                except IngestionJobError as error:
                    self.repository.update_task(str(task["id"]), status="failed", details={**details, "queue_error": str(error)})
                    self.repository.set_page_review_task_state(task_id=str(task["id"]), status="failed", error_code="QUEUE_FAILED", error_message=str(error))
                    failures.append(str(error))
            elif task["task_kind"] == "vision":
                try:
                    result = self.visual_analysis_service.queue_batch([str(task["source_ingestion_job_id"] or task["ingestion_job_id"])])[0]
                    visual = result.get("job") if isinstance(result.get("job"), dict) else {}
                    if result.get("outcome") in {"queued", "queued_retry", "already_queued"} and visual.get("id"):
                        self.repository.update_task(str(task["id"]), status="queued", visual_analysis_job_id=str(visual["id"]), details={**details, "queue_outcome": result.get("outcome")})
                        self.repository.set_page_review_task_state(task_id=str(task["id"]), status="queued")
                        queued += 1
                    else:
                        reason = str(result.get("reason") or "Vision task was not queued")
                        self.repository.update_task(str(task["id"]), status="failed", details={**details, "queue_result": result})
                        self.repository.set_page_review_task_state(task_id=str(task["id"]), status="failed", error_code="QUEUE_FAILED", error_message=reason)
                        failures.append(reason)
                except VisualAnalysisJobError as error:
                    self.repository.update_task(str(task["id"]), status="failed", details={**details, "queue_error": str(error)})
                    self.repository.set_page_review_task_state(task_id=str(task["id"]), status="failed", error_code="QUEUE_FAILED", error_message=str(error))
                    failures.append(str(error))
        self.repository.set_status(
            batch_id,
            status="queued" if queued else ("failed" if failures else "awaiting_approval"),
            error_code="BATCH_QUEUE_PARTIAL_FAILURE" if failures else None,
            error_message="; ".join(failures) if failures else None,
        )
        return queued, failures

    @staticmethod
    def _resolve_scheduled_for(*, schedule_mode: str, scheduled_for: datetime | None) -> datetime:
        shanghai = ZoneInfo("Asia/Shanghai")
        if schedule_mode == "nightly":
            now = datetime.now(shanghai)
            nightly_hour = settings.import_batch_nightly_hour
            candidate = datetime.combine(now.date(), time(hour=nightly_hour), tzinfo=shanghai)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc)
        if scheduled_for is None:
            raise ImportBatchError("Choose a future date and time for custom parsing")
        resolved = scheduled_for.replace(tzinfo=shanghai) if scheduled_for.tzinfo is None else scheduled_for
        if resolved <= datetime.now(resolved.tzinfo):
            raise ImportBatchError("Custom parsing time must be in the future")
        return resolved.astimezone(timezone.utc)

    def get(self, batch_id: str) -> dict[str, object]:
        batch = self._require_batch(batch_id)
        items, tasks = self.repository.list_items(batch_id), self.repository.list_tasks(batch_id)
        self._refresh_task_states(tasks)
        tasks = self.repository.list_tasks(batch_id)
        page_reviews = self.repository.list_page_reviews(batch_id)
        next_status = self._aggregate_status(batch, items, tasks)
        if batch["status"] not in {"awaiting_page_review", "awaiting_approval", "scheduled", "scheduling"} and next_status != batch["status"]:
            self.repository.set_status(batch_id, status=next_status)
            batch = self._require_batch(batch_id)
        return self._public_batch(batch, items=items, tasks=tasks, page_reviews=page_reviews)

    def list(self, *, limit: int, offset: int, project_id: str | None = None) -> dict[str, object]:
        rows = self.repository.list_batches(limit=limit, offset=offset, project_id=project_id)
        return {"items": [self._public_batch(row) for row in rows["items"]], "total": rows["total"]}

    def _refresh_task_states(self, tasks: list[dict[str, object]]) -> None:
        for task in tasks:
            if task["status"] not in {"queued", "processing"}:
                continue
            source_status = task.get("visual_analysis_status") if task["task_kind"] == "vision" else task.get("ingestion_status")
            if source_status in {"queued", "processing", "running", "submitted"} and task["status"] != "processing":
                self.repository.update_task(str(task["id"]), status="processing")
                self.repository.set_page_review_task_state(task_id=str(task["id"]), status="processing")
            elif source_status == "succeeded":
                self.repository.update_task(str(task["id"]), status="succeeded")
                self.repository.set_page_review_task_state(task_id=str(task["id"]), status="succeeded")
            elif source_status in {"failed", "cancelled"}:
                self.repository.update_task(str(task["id"]), status="failed")
                self.repository.set_page_review_task_state(task_id=str(task["id"]), status="failed", error_code=str(task.get("visual_analysis_error_code") or task.get("ingestion_error_code") or "TASK_FAILED"))

    @staticmethod
    def _aggregate_status(batch: dict[str, object], items: list[dict[str, object]], tasks: list[dict[str, object]]) -> str:
        if batch["status"] == "uploading":
            return "uploading"
        if batch["status"] == "awaiting_page_review":
            return "awaiting_page_review"
        if batch["status"] in {"scheduled", "scheduling"}:
            return str(batch["status"])
        if not tasks:
            return "failed" if any(item["outcome"] == "failed" for item in items) else "awaiting_approval"
        statuses = {str(task["status"]) for task in tasks}
        # A MinerU parent in the hybrid pipeline is not the end of the batch: the
        # reconciler still has to discover its extracted images and create GPT jobs.
        if any(
            dict(task.get("details") or {}).get("pipeline") == "mineru_then_gpt"
            and not dict(task.get("details") or {}).get("hybrid_vision_created")
            and task["status"] in {"queued", "processing", "succeeded"}
            for task in tasks
        ):
            return "processing"
        if statuses <= {"succeeded", "skipped"}:
            return "succeeded"
        if "failed" in statuses and statuses <= {"succeeded", "failed", "skipped"}:
            return "partially_succeeded" if "succeeded" in statuses else "failed"
        if "processing" in statuses:
            return "processing"
        if "queued" in statuses:
            return "queued"
        if "scheduled" in statuses:
            return "scheduled"
        return "awaiting_approval"

    def _manifest(self, batch: dict[str, object], items: list[dict[str, object]], tasks: list[dict[str, object]],
                  *, page_reviews: list[dict[str, object]] | None = None) -> dict[str, object]:
        return {
            "version": 2, "batch_id": str(batch["id"]),
            "project": {"external_key": str(batch["project_external_key"]), "name": str(batch["project_name"])},
            "force_reprocess": bool(batch.get("force_reprocess")),
            "items": [self._public_item(item) for item in items], "tasks": [self._public_task(task) for task in tasks],
            "page_reviews": [self._public_page_review(review) for review in (page_reviews or [])],
            "task_counts": {"mineru": sum(task["task_kind"] == "mineru" for task in tasks),
                            "vision": sum(task["task_kind"] == "vision" for task in tasks)},
            "external_export_requires_explicit_approval": True,
        }

    @staticmethod
    def _manifest_sha256(manifest: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _require_batch(self, batch_id: str) -> dict[str, object]:
        try:
            batch = self.repository.get_batch(batch_id)
        except (ValueError, TypeError) as error:
            raise ImportBatchError("Invalid import batch id") from error
        if not batch:
            raise ImportBatchError("Import batch not found")
        return batch

    @staticmethod
    def _safe_document_key(relative_path: str) -> str:
        return "".join(ch if ch.isalnum() else "-" for ch in relative_path)[:120].strip("-") or "source"

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        normal = value.replace("\\", "/").strip().lstrip("/")
        path = PurePosixPath(normal)
        if not normal or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ImportBatchError("source_relative_path must be a safe relative path")
        return path.as_posix()

    @staticmethod
    def _required(value: str, field_name: str) -> str:
        value = value.strip()
        if not value:
            raise ImportBatchError(f"{field_name} is required")
        return value

    @staticmethod
    def _json_safe(value: object) -> object:
        """Produce stable JSON-ready public values for manifest hashing and API output."""
        if isinstance(value, (UUID,)):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): ImportBatchService._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ImportBatchService._json_safe(item) for item in value]
        return value

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _public_page_review(review: dict[str, object], *, include_local_path: bool = False) -> dict[str, object]:
        fields = ("id", "item_id", "source_page_number", "preview_sha256", "preview_content_type",
                  "preview_size_bytes", "preview_width", "preview_height", "selected_processor", "review_status",
                  "mineru_task_id", "vision_task_id", "task_status", "error_code", "error_message", "created_at",
                  "updated_at", "source_file_name", "source_relative_path")
        result = {field: ImportBatchService._json_safe(review.get(field)) for field in fields}
        if include_local_path:
            result["preview_file_path"] = str(review.get("preview_file_path") or "")
        return result

    @staticmethod
    def _public_item(item: dict[str, object]) -> dict[str, object]:
        fields = ("id", "ingestion_job_id", "existing_ingestion_job_id", "source_relative_path", "source_file_name", "source_file_type", "source_sha256", "source_size_bytes", "outcome", "error_code", "error_message", "processing_plan", "created_at", "updated_at")
        return {field: ImportBatchService._json_safe(item.get(field)) for field in fields}

    @staticmethod
    def _public_task(task: dict[str, object]) -> dict[str, object]:
        fields = ("id", "item_id", "task_kind", "ingestion_job_id", "visual_analysis_job_id", "source_ingestion_job_id", "file_name", "file_type", "source_sha256", "source_size_bytes", "page_start", "page_end", "external_target", "status", "details", "ingestion_status", "visual_analysis_status", "visual_analysis_error_code", "ingestion_error_code", "created_at", "updated_at")
        return {field: ImportBatchService._json_safe(task.get(field)) for field in fields}

    def _public_batch(self, batch: dict[str, object], *, items: list[dict[str, object]] | None = None,
                      tasks: list[dict[str, object]] | None = None, page_reviews: list[dict[str, object]] | None = None) -> dict[str, object]:
        fields = ("id", "project_id", "project_external_key", "project_name", "force_reprocess", "status", "manifest", "manifest_sha256", "schedule_mode", "scheduled_for", "schedule_requested_at", "mineru_approved", "vision_approved", "error_code", "error_message", "created_at", "prepared_at", "approved_at", "completed_at", "updated_at")
        result = {field: self._json_safe(batch.get(field)) for field in fields}
        if items is not None:
            result["items"] = [self._public_item(item) for item in items]
        if tasks is not None:
            result["tasks"] = [self._public_task(task) for task in tasks]
        if page_reviews is not None:
            result["page_reviews"] = [self._public_page_review(review) for review in page_reviews]
        return result




