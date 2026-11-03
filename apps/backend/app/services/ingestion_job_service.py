"""Durable file-upload and MinerU task orchestration.

No source file is sent to an external provider at upload time. Submission is an
explicit, auditable second operation so callers can enforce their data-export policy.
"""

from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings
from app.providers.parser.base import ParserProvider, ParserProviderError
from app.repositories.rag_repository import RagRepository
from app.services.image_asset_materializer import materialize_archive_image_assets
from app.services.mineru_result_normalizer import MinerUResultNormalizer
from app.services.rag_service import IngestionService


class IngestionJobError(RuntimeError):
    pass


class IngestionJobService:
    """Archive inputs and apply the formal content-routing policy.

    Routes are intentionally recorded on every job so a later replay or Qdrant
    rebuild does not have to infer whether an image was OCR'd or vision-analysed.
    """

    IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
    DOCUMENT_PROFILES = frozenset({"auto", "document"})
    IMAGE_PROFILES = frozenset({"auto", "vision", "ocr_and_vision"})

    def __init__(
        self,
        repository: RagRepository,
        parser_provider: ParserProvider,
        ingestion_service: IngestionService,
        normalizer: MinerUResultNormalizer | None = None,
    ) -> None:
        self.repository = repository
        self.parser_provider = parser_provider
        self.ingestion_service = ingestion_service
        self.normalizer = normalizer or MinerUResultNormalizer()

    async def create_from_upload(
        self,
        *,
        file: UploadFile,
        project_external_key: str,
        project_name: str,
        document_external_key: str | None,
        access_scope_json: str | None,
        document_metadata_json: str | None,
        processing_profile: str | None = None,
    ) -> dict[str, object]:
        file_name = Path(file.filename or "upload").name
        extension = Path(file_name).suffix.lower().lstrip(".")
        if extension not in settings.ingestion_allowed_extensions:
            supported = ", ".join(settings.ingestion_allowed_extensions)
            raise IngestionJobError(f"Unsupported file type '.{extension}'. Supported: {supported}")
        trace_id = str(uuid4())
        destination = settings.data_root / "documents" / "incoming" / trace_id / "original" / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        total = 0
        try:
            with destination.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.ingestion_max_upload_bytes:
                        target.close()
                        destination.unlink(missing_ok=True)
                        raise IngestionJobError(
                            f"Upload exceeds the configured limit of {settings.ingestion_max_upload_bytes} bytes"
                        )
                    digest.update(chunk)
                    target.write(chunk)
        finally:
            await file.close()
        processing_route, route_reason = self._resolve_processing_route(
            extension=extension,
            source_size_bytes=total,
            processing_profile=processing_profile,
        )
        project = {
            "external_key": self._required(project_external_key, "project_external_key"),
            "name": self._required(project_name, "project_name"),
            "metadata": {},
        }
        document = {
            "external_key": document_external_key or f"upload-{trace_id}",
            "source_uri": f"local://documents/incoming/{trace_id}/original/{file_name}",
            "file_name": file_name,
            "file_type": extension,
            "metadata": self._json_object(document_metadata_json, "document_metadata_json"),
            "access_scope": self._json_object(access_scope_json, "access_scope_json"),
        }
        return self.repository.create_ingestion_job(
            project=project,
            document=document,
            provider_name=(self.parser_provider.provider_name if processing_route != "vision" else "gpt_vision"),
            source_sha256=digest.hexdigest(),
            source_file_name=file_name,
            source_file_path=str(destination),
            source_content_type=file.content_type,
            source_size_bytes=total,
            processing_route=processing_route,
            route_reason=route_reason,
            trace_id=trace_id,
        )

    def create_from_archived_file(
        self,
        *,
        source_path: Path,
        source_sha256: str,
        source_size_bytes: int,
        source_content_type: str | None,
        project_external_key: str,
        project_name: str,
        source_relative_path: str,
        document_metadata: dict[str, object] | None = None,
        access_scope: dict[str, object] | None = None,
        processing_profile: str | None = None,
        document_external_key: str | None = None,
    ) -> dict[str, object]:
        """Create a job pointing to a locally staged batch file; never copies or exports it."""
        source_path = source_path.resolve()
        allowed_roots = [
            (settings.data_root / "documents" / "import-batches").resolve(),
            (settings.data_root / "documents" / "derived").resolve(),
        ]
        if not any(self._is_relative_to(source_path, root) for root in allowed_roots):
            raise IngestionJobError("Archived batch source is outside approved local storage")
        if not source_path.is_file():
            raise IngestionJobError("Archived batch source file is missing")
        if source_size_bytes < 0 or source_path.stat().st_size != source_size_bytes:
            raise IngestionJobError("Archived batch source size no longer matches the uploaded file")
        if self._file_sha256(source_path) != source_sha256:
            raise IngestionJobError("Archived batch source SHA-256 no longer matches the uploaded file")
        extension = source_path.suffix.lower().lstrip(".")
        if extension not in settings.ingestion_allowed_extensions:
            raise IngestionJobError(f"Unsupported file type '.{extension}'")
        processing_route, route_reason = self._resolve_processing_route(
            extension=extension, source_size_bytes=source_size_bytes, processing_profile=processing_profile
        )
        try:
            storage_relative_path = source_path.relative_to(settings.data_root.resolve()).as_posix()
        except ValueError as error:
            raise IngestionJobError("Archived batch source is outside DATA_ROOT") from error
        safe_key = "".join(ch if ch.isalnum() else "-" for ch in source_relative_path)[:120].strip("-") or "source"
        return self.repository.create_ingestion_job(
            project={"external_key": self._required(project_external_key, "project_external_key"),
                     "name": self._required(project_name, "project_name"), "metadata": {}},
            document={"external_key": (document_external_key or f"{project_external_key}-{safe_key}-{source_sha256[:12]}")[:200],
                      "source_uri": f"local://{storage_relative_path}", "file_name": source_path.name,
                      "file_type": extension, "metadata": dict(document_metadata or {}),
                      "access_scope": dict(access_scope or {})},
            provider_name=(self.parser_provider.provider_name if processing_route != "vision" else "gpt_vision"),
            source_sha256=source_sha256, source_file_name=source_path.name, source_file_path=str(source_path),
            source_content_type=source_content_type, source_size_bytes=source_size_bytes,
            processing_route=processing_route, route_reason=route_reason, trace_id=str(uuid4()),
        )
    def reimport(self, job_id: str) -> dict[str, object]:
        """Create a new local import job from an existing archived source file.

        This does not transmit source bytes externally. A new immutable archive path
        and document key make the retry visible as its own record while preserving the
        failed/original attempt for audit and troubleshooting.
        """
        original = self.get(job_id)
        source_path = Path(str(original["source_file_path"])).resolve()
        allowed_root = (settings.data_root / "documents" / "incoming").resolve()
        try:
            source_path.relative_to(allowed_root)
        except ValueError as error:
            raise IngestionJobError("Archived source file is outside the approved local import storage") from error
        if not source_path.is_file():
            raise IngestionJobError("Archived source file is no longer available for reimport")

        trace_id = str(uuid4())
        file_name = Path(str(original["source_file_name"])).name
        destination = settings.data_root / "documents" / "incoming" / trace_id / "original" / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            copyfile(source_path, destination)
        except OSError as error:
            raise IngestionJobError("Unable to prepare a new local archive for reimport") from error

        document_metadata = original.get("document_metadata")
        metadata = dict(document_metadata) if isinstance(document_metadata, dict) else {}
        metadata["reimport_of_ingestion_job_id"] = str(original["id"])
        document_external_key = f"{original['document_external_key']}-reimport-{trace_id[:8]}"
        return self.repository.create_ingestion_job(
            project={
                "external_key": str(original["project_external_key"]),
                "name": str(original["project_name"]),
                "metadata": {},
            },
            document={
                "external_key": document_external_key,
                "source_uri": f"local://documents/incoming/{trace_id}/original/{file_name}",
                "file_name": file_name,
                "file_type": str(original["file_type"]),
                "metadata": metadata,
                "access_scope": original.get("document_access_scope") if isinstance(original.get("document_access_scope"), dict) else {},
            },
            provider_name=(self.parser_provider.provider_name if original["processing_route"] != "vision" else "gpt_vision"),
            source_sha256=str(original["source_sha256"]),
            source_file_name=file_name,
            source_file_path=str(destination),
            source_content_type=str(original["source_content_type"]) if original.get("source_content_type") else None,
            source_size_bytes=int(original["source_size_bytes"]),
            processing_route=str(original["processing_route"]),
            route_reason=f"Reimport of {original['id']}: {original['route_reason']}",
            trace_id=trace_id,
        )
    def get(self, job_id: str) -> dict[str, object]:
        job = self.repository.get_ingestion_job(job_id)
        if not job:
            raise IngestionJobError("Ingestion job not found")
        return job

    def enqueue(self, job_id: str) -> dict[str, object]:
        """Put one explicitly approved document into the local submission queue.

        This operation never uploads the source inline, so the Admin UI can return
        immediately and show a durable queue state.
        """
        job = self.get(job_id)
        if job["processing_route"] == "vision":
            raise IngestionJobError(
                "This image is routed directly to GPT vision. Use the image-analysis queue with explicit approval instead."
            )
        if job["processing_route"] == "local_preprocess":
            raise IngestionJobError("This source needs a locally prepared PDF derivative before MinerU submission.")
        if job["status"] not in {"created", "failed", "queued", "processing", "submitted"}:
            raise IngestionJobError(f"Job cannot be queued from status '{job['status']}'")
        self._require_mineru_size_limit(int(job["source_size_bytes"]))
        if not self._provider_is_configured():
            raise IngestionJobError(
                "MinerU is not configured. Set MINERU_API_TOKEN in .env before submitting a file externally."
            )
        return self.repository.enqueue_ingestion_submission(job_id)

    def submit(self, job_id: str) -> dict[str, object]:
        """Execute a parser submission after the local worker has claimed it."""
        job = self.get(job_id)
        if job["status"] != "processing":
            raise IngestionJobError(f"Job cannot be dispatched from status '{job['status']}'")
        if job["processing_route"] == "vision":
            raise IngestionJobError("Direct-vision images must not be sent to the parser service")
        if job["processing_route"] == "local_preprocess":
            raise IngestionJobError("Local-preprocess source must not be sent to the parser service")
        self._require_mineru_size_limit(int(job["source_size_bytes"]))
        if not self._provider_is_configured():
            raise IngestionJobError("MinerU is not configured. Set MINERU_API_TOKEN in .env before submitting a file externally.")
        try:
            submission = self.parser_provider.submit(
                Path(str(job["source_file_path"])),
                file_name=str(job["source_file_name"]),
                content_type=str(job["source_content_type"] or "application/octet-stream"),
            )
        except ParserProviderError as error:
            self.repository.record_ingestion_failure(job_id, error_code="MINERU_SUBMIT_FAILED", error_message=str(error))
            raise IngestionJobError(str(error)) from error
        self.repository.record_ingestion_submission(
            job_id,
            task_id=submission.task_id,
            batch_id=submission.batch_id,
            provider_request={
                "source_sha256": job["source_sha256"],
                "source_file_name": job["source_file_name"],
                "source_size_bytes": job["source_size_bytes"],
                "processing_route": job["processing_route"],
                "model_version": settings.mineru_model_version,
                "language": settings.mineru_language,
                "enable_formula": settings.mineru_enable_formula,
                "enable_table": settings.mineru_enable_table,
            },
            provider_response=submission.raw_response,
        )
        return self.get(job_id)

    def run_queued_submission(self, job_id: str) -> dict[str, object] | None:
        """Claim and dispatch one queued document. Intended only for the local worker."""
        if not self.repository.claim_ingestion_submission(job_id):
            return None
        try:
            return self.submit(job_id)
        except IngestionJobError:
            # submit() has already persisted the provider failure when applicable.
            raise

    def delete_batch(self, job_ids: list[str]) -> list[dict[str, object]]:
        """Remove only local Admin records; repository protects indexed KB content."""
        unique_ids = list(dict.fromkeys(job_id for job_id in job_ids if job_id))
        return [self.repository.delete_unmaterialized_ingestion_job(job_id) for job_id in unique_ids]

    def submit_batch(self, job_ids: list[str]) -> list[dict[str, object]]:
        """Queue documents independently; no file bytes are exported inline."""
        results: list[dict[str, object]] = []
        for job_id in job_ids:
            try:
                job = self.get(job_id)
                if job["processing_route"] == "vision":
                    results.append({
                        "id": str(job["id"]), "outcome": "skipped",
                        "reason": "图片需通过“分析图片”并再次明确授权后提交到外部视觉模型。",
                    })
                    continue
                queued = self.enqueue(job_id)
                results.append({"id": str(job["id"]), **queued})
            except IngestionJobError as error:
                results.append({"id": job_id, "outcome": "failed", "reason": str(error)})
        return results

    def poll(self, job_id: str) -> dict[str, object]:
        job = self.get(job_id)
        if not job["provider_task_id"]:
            raise IngestionJobError("Job has not been submitted to MinerU")
        if job["status"] in {"succeeded", "cancelled"}:
            return job
        try:
            status = self.parser_provider.poll(
                str(job["provider_task_id"]),
                batch_id=str(job["provider_batch_id"]) if job.get("provider_batch_id") else None,
            )
            result_path = None
            if status.status == "succeeded" and status.result_url:
                result_file = settings.data_root / "mineru-results" / job_id / "result.zip"
                downloaded = self.parser_provider.download_result(status.result_url, result_file)
                result_path = str(downloaded)
                result_sha256 = self._file_sha256(downloaded)
                self.repository.add_document_asset(
                    document_id=str(job["document_id"]),
                    ingestion_job_id=job_id,
                    asset_kind="parser_result",
                    file_name=downloaded.name,
                    storage_path=result_path,
                    content_type="application/octet-stream",
                    content_sha256=result_sha256,
                    size_bytes=downloaded.stat().st_size,
                    metadata={
                        "provider": self.parser_provider.provider_name,
                        "provider_task_id": job["provider_task_id"],
                        "model_version": settings.mineru_model_version,
                        "processing_route": job["processing_route"],
                    },
                )
            self.repository.record_ingestion_poll(
                job_id,
                status=status.status,
                provider_response=status.raw_response,
                result_path=result_path,
                error_code=status.error_code,
                error_message=status.error_message,
            )
        except ParserProviderError as error:
            self.repository.record_ingestion_failure(
                job_id, error_code="MINERU_POLL_FAILED", error_message=str(error)
            )
            raise IngestionJobError(str(error)) from error
        return self.get(job_id)

    def materialize(self, job_id: str) -> dict[str, object]:
        """Normalize a downloaded MinerU result and reuse the parent-child ingest path."""
        job = self.get(job_id)
        if job["document_version_id"]:
            return {"job": job, "materialized": False, "reason": "already_materialized"}
        if job["processing_route"] == "vision":
            raise IngestionJobError("Direct-vision images do not have a MinerU result to materialize")
        if job["status"] != "succeeded" or not job["result_path"]:
            raise IngestionJobError("A succeeded job with a downloaded MinerU result is required")
        result_path = Path(str(job["result_path"]))
        if not result_path.is_file():
            raise IngestionJobError("Archived MinerU result file is missing")
        normalized = self.normalizer.normalize_result(result_path)
        document_metadata = job["document_metadata"]
        chunks = self._map_original_pdf_page_numbers(
            normalized.chunks,
            document_metadata if isinstance(document_metadata, dict) else {},
        )
        if not chunks:
            raise IngestionJobError("MinerU result did not contain indexable structured content or Markdown")
        document_scope = job["document_access_scope"]
        payload = {
            "project": {
                "external_key": job["project_external_key"],
                "name": job["project_name"],
                "metadata": {},
            },
            "document": {
                "external_key": job["document_external_key"],
                "source_uri": job["source_uri"],
                "file_name": job["file_name"],
                "file_type": job["file_type"],
                "metadata": document_metadata if isinstance(document_metadata, dict) else {},
                "access_scope": document_scope if isinstance(document_scope, dict) else {},
            },
            "parser": {
                "provider": self.parser_provider.provider_name,
                "model": "MinerU Precision API",
                "version": settings.mineru_model_version,
                "parsed_content_uri": str(result_path),
                "parsed_payload": {
                    "ingestion_job_id": job_id,
                    "provider_task_id": job["provider_task_id"],
                    "source_sha256": job["source_sha256"],
                    "model_version": settings.mineru_model_version,
                    "processing_route": job["processing_route"],
                    "parser_structure": normalized.parser_structure,
                },
                "source_sha256": job["source_sha256"],
                "source_storage_key": job["source_uri"],
                "source_file_size_bytes": job["source_size_bytes"],
                "parse_status": "materialized",
                "source_confidence": 0.8,
            },
            "chunks": chunks,
        }
        result = self.ingestion_service.ingest(payload)
        document_version_id = str(result["document_version_id"])
        self.repository.link_ingestion_job_document_version(job_id, document_version_id)
        # MinerU image binaries, where supplied in the downloaded ZIP, are copied to
        # internal storage. Missing binaries never discard their structured page/bbox metadata.
        image_assets = materialize_archive_image_assets(
            result_path=result_path,
            output_dir=settings.data_root / "documents" / "derived" / str(job["trace_id"]) / "mineru-images",
            image_assets=self._map_original_pdf_page_numbers(
                [dict(item) for item in normalized.image_assets],
                document_metadata if isinstance(document_metadata, dict) else {},
            ),
        )
        stored_image_assets = self.repository.record_normalized_image_assets(
            document_id=str(result["document_id"]),
            document_version_id=document_version_id,
            ingestion_job_id=job_id,
            image_assets=image_assets,
        )
        response: dict[str, object] = {
            "job": self.get(job_id),
            "materialized": True,
            "ingestion": result,
            "parser_structure": normalized.parser_structure,
            "image_asset_count": len(stored_image_assets),
        }
        if job["processing_route"] == "mineru_vlm_then_vision":
            response["next_action"] = {
                "operation": "create_visual_analysis_job",
                "endpoint": "/api/v1/visual-analysis-jobs",
                "ingestion_job_id": job_id,
                "note": "OCR has been materialized. Creating the visual job is local; GPT submission remains a separate explicit approval step.",
            }
        return response

    @staticmethod
    def _map_original_pdf_page_numbers(
        records: list[dict[str, object]], document_metadata: dict[str, object]
    ) -> list[dict[str, object]]:
        """Map page numbers from a MinerU page-range PDF back to its original PDF.

        Page-range derivatives are intentionally opaque to MinerU: their first page is
        always reported as page 1.  The immutable ``source_page_numbers`` manifest is
        therefore the authoritative mapping for chunks, source locators and extracted
        image assets.  Unknown or out-of-range values are retained and marked instead
        of being guessed, which keeps citations auditable.
        """
        raw_pages = document_metadata.get("source_page_numbers")
        if not isinstance(raw_pages, list):
            return records
        source_pages: list[int] = []
        for value in raw_pages:
            try:
                page = int(value)
            except (TypeError, ValueError):
                return records
            if page <= 0:
                return records
            source_pages.append(page)
        if not source_pages:
            return records

        def map_page(value: object) -> tuple[object, bool]:
            try:
                internal_page = int(value)
            except (TypeError, ValueError):
                return value, False
            if 1 <= internal_page <= len(source_pages):
                return source_pages[internal_page - 1], True
            return value, False

        def remap_locator(locator: object) -> object:
            if not isinstance(locator, dict):
                return locator
            mapped = dict(locator)
            for key in ("page", "page_number", "page_start", "page_end"):
                if key not in mapped:
                    continue
                original, valid = map_page(mapped[key])
                if valid:
                    mapped[key] = original
            return mapped

        def remap_record(record: dict[str, object]) -> dict[str, object]:
            for field in ("page_start", "page_end", "page_number"):
                if field not in record or record[field] is None:
                    continue
                internal_value = record[field]
                original_value, valid = map_page(internal_value)
                metadata = record.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                else:
                    metadata = dict(metadata)
                if valid:
                    metadata[f"mineru_subpdf_{field}"] = internal_value
                    record[field] = original_value
                else:
                    metadata.setdefault("page_mapping_warning", "MinerU page is outside the approved source-page manifest")
                    metadata.setdefault(f"mineru_subpdf_{field}", internal_value)
                record["metadata"] = metadata
            if "source_locator" in record:
                record["source_locator"] = remap_locator(record["source_locator"])
            children = record.get("children")
            if isinstance(children, list):
                record["children"] = [
                    remap_record(child) if isinstance(child, dict) else child
                    for child in children
                ]
            return record

        return [remap_record(record) if isinstance(record, dict) else record for record in records]

    @classmethod
    def _resolve_processing_route(
        cls, *, extension: str, source_size_bytes: int, processing_profile: str | None
    ) -> tuple[str, str]:
        profile = (processing_profile or "auto").strip().lower()
        if extension not in cls.IMAGE_EXTENSIONS:
            if profile not in cls.DOCUMENT_PROFILES:
                raise IngestionJobError(
                    "Documents accept processing_profile=auto or document; use ocr_and_vision only for PNG/JPG/JPEG/WEBP."
                )
            return "mineru_vlm", "document format routed to MinerU Precision API with model_version=vlm"
        if profile not in cls.IMAGE_PROFILES:
            raise IngestionJobError("Images accept processing_profile=auto, vision, or ocr_and_vision")
        if profile == "ocr_and_vision" and source_size_bytes <= settings.mineru_precision_max_file_bytes:
            return "mineru_vlm_then_vision", "text-heavy design image: MinerU vlm OCR before GPT visual analysis"
        if profile == "ocr_and_vision":
            return "vision", "image exceeds MinerU Precision API size limit; routed directly to GPT vision"
        return "vision", "ordinary design image routed directly to GPT vision"

    @staticmethod
    def _result_to_markdown(result_path: Path) -> str:
        try:
            return MinerUResultNormalizer.result_to_markdown(result_path)
        except ValueError as error:
            raise IngestionJobError(str(error)) from error

    def _provider_is_configured(self) -> bool:
        checker = getattr(self.parser_provider, "is_configured", None)
        return bool(checker()) if callable(checker) else True

    @staticmethod
    def _require_mineru_size_limit(source_size_bytes: int) -> None:
        if source_size_bytes <= settings.mineru_precision_max_file_bytes:
            return
        raise IngestionJobError(
            "Source exceeds the MinerU Precision API limit of "
            f"{settings.mineru_precision_max_file_bytes} bytes. Do not submit the original file; "
            "create a locally normalized PDF page-range derivative first."
        )

    @staticmethod
    def _required(value: str, field_name: str) -> str:
        value = value.strip()
        if not value:
            raise IngestionJobError(f"{field_name} is required")
        return value

    @staticmethod
    def _json_object(raw: str | None, field_name: str) -> dict[str, Any]:
        if not raw or not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise IngestionJobError(f"{field_name} must be a JSON object") from error
        if not isinstance(value, dict):
            raise IngestionJobError(f"{field_name} must be a JSON object")
        return value

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


