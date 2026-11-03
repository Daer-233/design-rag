from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from starlette.datastructures import UploadFile

from app.services.document_page_renderer import RenderedDocument, RenderedDocumentPage
from app.services.import_batch_service import ImportBatchError, ImportBatchService
from app.services.vision_image_preprocessor import VisionImageDerivative


class _Repository:
    def __init__(self, *, status: str = "awaiting_approval", tasks: list[dict[str, object]] | None = None,
                 items: list[dict[str, object]] | None = None) -> None:
        self.batch_id = str(uuid4())
        self.batch: dict[str, object] = {
            "id": self.batch_id,
            "project_id": uuid4(),
            "project_external_key": "crland-top-clubhouse-local-test",
            "project_name": "02华润顶层会所（本地预处理测试）",
            "force_reprocess": False,
            "status": status,
            "manifest": None,
            "manifest_sha256": "a" * 64,
            "schedule_mode": "immediate",
            "scheduled_for": None,
            "schedule_requested_at": None,
            "error_code": None,
            "error_message": None,
            "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
            "prepared_at": None,
            "approved_at": None,
            "completed_at": None,
            "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
        }
        self.items = deepcopy(items or [])
        self.tasks = deepcopy(tasks or [])
        self.page_reviews: list[dict[str, object]] = []
        self.status_updates: list[str] = []

    def get_batch(self, batch_id: str) -> dict[str, object] | None:
        return deepcopy(self.batch) if batch_id == self.batch_id else None

    def list_items(self, batch_id: str) -> list[dict[str, object]]:
        assert batch_id == self.batch_id
        return deepcopy(self.items)

    def list_tasks(self, batch_id: str) -> list[dict[str, object]]:
        assert batch_id == self.batch_id
        return deepcopy(self.tasks)

    def list_page_reviews(self, batch_id: str) -> list[dict[str, object]]:
        assert batch_id == self.batch_id
        return deepcopy(self.page_reviews)

    def add_page_review(self, *, batch_id: str, item_id: str, source_page_number: int,
                        preview_file_path: str, preview_sha256: str, preview_content_type: str,
                        preview_size_bytes: int, preview_width: int, preview_height: int,
                        selected_processor: str) -> dict[str, object]:
        assert batch_id == self.batch_id
        review = {
            "id": str(uuid4()), "batch_id": batch_id, "item_id": item_id,
            "source_page_number": source_page_number, "preview_file_path": preview_file_path,
            "preview_sha256": preview_sha256, "preview_content_type": preview_content_type,
            "preview_size_bytes": preview_size_bytes, "preview_width": preview_width,
            "preview_height": preview_height, "selected_processor": selected_processor,
            "review_status": "pending", "mineru_task_id": None, "vision_task_id": None,
            "task_status": "pending", "error_code": None, "error_message": None,
            "source_file_name": "clubhouse.pdf", "source_relative_path": "方案/clubhouse.pdf",
            "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
        }
        self.page_reviews.append(review)
        return deepcopy(review)

    def get_page_review(self, *, batch_id: str, page_review_id: str) -> dict[str, object] | None:
        assert batch_id == self.batch_id
        row = next((item for item in self.page_reviews if item["id"] == page_review_id), None)
        return deepcopy(row) if row else None

    def update_page_reviews(self, *, batch_id: str, page_review_ids: list[str], selected_processor: str) -> None:
        assert batch_id == self.batch_id
        for row in self.page_reviews:
            if row["id"] in page_review_ids:
                row["selected_processor"] = selected_processor

    def confirm_page_reviews(self, *, batch_id: str) -> None:
        assert batch_id == self.batch_id
        for row in self.page_reviews:
            row["review_status"] = "confirmed"
        self.batch["status"] = "awaiting_approval"

    def link_page_review_task(self, *, page_review_id: str, processor: str, task_id: str) -> None:
        for row in self.page_reviews:
            if row["id"] == page_review_id:
                row[f"{processor}_task_id"] = task_id
                row["task_status"] = "awaiting_approval"

    def set_page_review_task_state(self, **_kwargs: object) -> None:
        return None

    def delete_awaiting_approval_tasks(self, *, batch_id: str) -> None:
        assert batch_id == self.batch_id
        self.tasks = [task for task in self.tasks if task["status"] != "awaiting_approval"]

    def add_task(self, *, batch_id: str, item_id: str, task_kind: str, ingestion_job_id: str,
                 source_ingestion_job_id: str, file_name: str, file_type: str, source_sha256: str,
                 source_size_bytes: int, external_target: str, details: dict[str, object],
                 page_start: int | None = None, page_end: int | None = None) -> dict[str, object]:
        assert batch_id == self.batch_id
        task = {
            "id": str(uuid4()), "item_id": item_id, "task_kind": task_kind,
            "ingestion_job_id": ingestion_job_id, "visual_analysis_job_id": None,
            "source_ingestion_job_id": source_ingestion_job_id, "file_name": file_name,
            "file_type": file_type, "source_sha256": source_sha256,
            "source_size_bytes": source_size_bytes, "page_start": page_start, "page_end": page_end,
            "external_target": external_target, "status": "awaiting_approval", "details": deepcopy(details),
            "ingestion_status": "created", "visual_analysis_status": None,
            "visual_analysis_error_code": None, "ingestion_error_code": None,
            "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
        }
        self.tasks.append(task)
        return {"id": task["id"]}

    def update_task(self, task_id: str, *, status: str, visual_analysis_job_id: str | None = None,
                    details: dict[str, object] | None = None) -> None:
        for task in self.tasks:
            if task["id"] == task_id:
                task["status"] = status
                if visual_analysis_job_id is not None:
                    task["visual_analysis_job_id"] = visual_analysis_job_id
                if details is not None:
                    task["details"] = deepcopy(details)
                return
        raise AssertionError(f"Unknown task {task_id}")

    def update_item(self, item_id: str, *, processing_plan: dict[str, object] | None = None,
                    outcome: str | None = None, error_code: str | None = None,
                    error_message: str | None = None) -> None:
        for item in self.items:
            if str(item["id"]) == item_id:
                if processing_plan is not None:
                    item["processing_plan"] = deepcopy(processing_plan)
                if outcome is not None:
                    item["outcome"] = outcome
                item["error_code"] = error_code
                item["error_message"] = error_message
                return
        raise AssertionError(f"Unknown item {item_id}")

    def schedule_approved_batch(self, *, batch_id: str, schedule_mode: str, scheduled_for: object) -> None:
        assert batch_id == self.batch_id
        self.batch.update(status="scheduled", schedule_mode=schedule_mode, scheduled_for=scheduled_for,
                          schedule_requested_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc))
        self.status_updates.append("scheduled")

    def claim_due_scheduled_batches(self, *, limit: int = 20) -> list[str]:
        if self.batch["status"] in {"scheduled", "scheduling"}:
            scheduled_for = self.batch.get("scheduled_for")
            if self.batch["status"] == "scheduling" or (scheduled_for and scheduled_for <= datetime.now(timezone.utc)):
                self.batch["status"] = "scheduling"
                return [self.batch_id]
        return []

    def set_prepared_manifest(self, *, batch_id: str, manifest: dict[str, object], manifest_sha256: str,
                              status: str = "awaiting_approval") -> None:
        assert batch_id == self.batch_id
        self.batch.update(status=status, manifest=deepcopy(manifest), manifest_sha256=manifest_sha256)

    def set_status(self, batch_id: str, *, status: str, error_code: str | None = None,
                   error_message: str | None = None) -> None:
        assert batch_id == self.batch_id
        self.batch.update(status=status, error_code=error_code, error_message=error_message)
        self.status_updates.append(status)


class _IngestionRepository:
    def __init__(self) -> None:
        self.derivative_calls: list[dict[str, object]] = []

    def ensure_visual_analysis_derivative_asset(self, **kwargs: object) -> dict[str, object]:
        self.derivative_calls.append(dict(kwargs))
        return {"id": str(uuid4())}


class _Ingestion:
    def __init__(self) -> None:
        self.repository = _IngestionRepository()
        self.enqueue_calls: list[str] = []
        self.job_id = str(uuid4())

    def get(self, job_id: str) -> dict[str, object]:
        assert job_id == self.job_id
        return {
            "id": job_id, "source_file_path": "C:/local-only/crland.jpg", "source_file_name": "crland.jpg",
            "file_type": "jpg", "source_sha256": "b" * 64, "source_size_bytes": 2048,
            "processing_route": "vision", "route_reason": "Image source",
        }

    def enqueue(self, job_id: str) -> dict[str, object]:
        self.enqueue_calls.append(job_id)
        return {"outcome": "queued"}




class _DocumentIngestion(_Ingestion):
    def __init__(self) -> None:
        super().__init__()
        self.created_pages: list[dict[str, object]] = []

    def get(self, job_id: str) -> dict[str, object]:
        assert job_id == self.job_id
        return {
            "id": job_id, "source_file_path": "C:/local-only/clubhouse.pptx",
            "source_file_name": "clubhouse.pptx", "file_type": "pptx",
            "source_sha256": "b" * 64, "source_size_bytes": 4096,
            "processing_route": "mineru_vlm", "route_reason": "Document source",
        }

    def create_from_archived_file(self, **kwargs: object) -> dict[str, object]:
        self.created_pages.append(dict(kwargs))
        return {
            "id": str(uuid4()), "processing_route": "vision",
            "route_reason": "ordinary design image routed directly to GPT vision",
        }


class _DocumentRenderer:
    dpi = 144

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def render(self, source_path: Path, *, output_root: Path, source_sha256: str) -> RenderedDocument:
        self.calls.append({"source_path": source_path, "output_root": output_root, "source_sha256": source_sha256})
        pages = [
            RenderedDocumentPage(1, output_root / "hash" / "pages" / "page-1.jpg", "1" * 64, 1600, 900, 1000),
            RenderedDocumentPage(2, output_root / "hash" / "pages" / "page-2.jpg", "2" * 64, 1600, 900, 1100),
        ]
        return RenderedDocument(source_path, output_root / "hash" / "pdf" / "clubhouse.pdf", "pptx", 2, pages, True)


class _Vision:
    def __init__(self) -> None:
        self.queue_calls: list[list[str]] = []

    def queue_batch(self, job_ids: list[str]) -> list[dict[str, object]]:
        self.queue_calls.append(job_ids)
        return [{"outcome": "queued", "job": {"id": str(uuid4())}}]


class _Preprocessor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def prepare(self, source_path: Path, *, source_sha256: str) -> VisionImageDerivative:
        self.calls.append((source_path, source_sha256))
        return VisionImageDerivative(
            source_path=source_path,
            derivative_path=Path("C:/local-only/crland-analysis.jpg"),
            source_sha256=source_sha256,
            derivative_sha256="c" * 64,
            source_width=2400,
            source_height=1600,
            derivative_width=2048,
            derivative_height=1365,
            content_type="image/jpeg",
            size_bytes=1024,
            orientation_corrected=False,
            max_dimension=2048,
            derivation_type="normalized_image",
            generator_version="test-v1",
        )


def _task(kind: str, ingestion_job_id: str) -> dict[str, object]:
    return {
        "id": str(uuid4()), "item_id": str(uuid4()), "task_kind": kind,
        "ingestion_job_id": ingestion_job_id, "visual_analysis_job_id": None,
        "source_ingestion_job_id": ingestion_job_id, "file_name": f"{kind}.jpg",
        "file_type": "jpg", "source_sha256": "d" * 64, "source_size_bytes": 10,
        "page_start": None, "page_end": None, "external_target": kind, "status": "awaiting_approval",
        "details": {}, "ingestion_status": "created", "visual_analysis_status": None,
        "visual_analysis_error_code": None, "ingestion_error_code": None,
        "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    }


def test_prepare_image_creates_only_local_derivative_and_awaiting_approval_task() -> None:
    ingestion, vision, preprocessor = _Ingestion(), _Vision(), _Preprocessor()
    item_id = uuid4()
    repository = _Repository(status="uploading", items=[{
        "id": item_id, "ingestion_job_id": ingestion.job_id, "existing_ingestion_job_id": None,
        "source_relative_path": "成图/crland.jpg", "source_file_name": "crland.jpg", "source_file_type": "jpg",
        "source_sha256": "b" * 64, "source_size_bytes": 2048, "outcome": "archived",
        "error_code": None, "error_message": None, "processing_plan": {"processing_route": "vision"},
        "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc), "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    }])
    service = ImportBatchService(repository, ingestion, vision, preprocessor)

    result = service.prepare(repository.batch_id)

    assert result["status"] == "awaiting_approval"
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["task_kind"] == "vision"
    assert result["tasks"][0]["status"] == "awaiting_approval"
    assert preprocessor.calls and ingestion.repository.derivative_calls
    assert ingestion.enqueue_calls == []
    assert vision.queue_calls == []


def test_approval_requires_manifest_and_queues_only_explicitly_authorized_kind() -> None:
    ingestion, vision = _Ingestion(), _Vision()
    repository = _Repository(tasks=[_task("mineru", ingestion.job_id), _task("vision", ingestion.job_id)])
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor())

    with pytest.raises(ImportBatchError, match="manifest has changed"):
        service.approve(batch_id=repository.batch_id, manifest_sha256="e" * 64,
                        approve_mineru=True, approve_vision=True)
    assert ingestion.enqueue_calls == []
    assert vision.queue_calls == []

    result = service.approve(batch_id=repository.batch_id, manifest_sha256="a" * 64,
                             approve_mineru=False, approve_vision=True)

    assert ingestion.enqueue_calls == []
    assert vision.queue_calls == [[ingestion.job_id]]
    assert result["status"] == "queued"
    statuses = {task["task_kind"]: task["status"] for task in result["tasks"]}
    assert statuses == {"mineru": "awaiting_approval", "vision": "queued"}


def test_manifest_json_is_stable_for_uuid_datetime_and_nested_data() -> None:
    manifest = {
        "id": uuid4(), "when": datetime(2026, 8, 27, 10, 30, tzinfo=timezone.utc),
        "nested": {uuid4(): [datetime(2026, 8, 27, tzinfo=timezone.utc)]},
    }
    safe = ImportBatchService._json_safe(manifest)

    assert isinstance(safe, dict)
    assert ImportBatchService._manifest_sha256(safe) == ImportBatchService._manifest_sha256(safe)



def test_prepare_pptx_creates_mineru_and_per_page_vision_tasks_without_queueing(monkeypatch) -> None:
    from app.services.import_batch_service import settings as import_settings

    monkeypatch.setattr(import_settings, "document_page_vision_enabled", True)
    ingestion, vision, renderer = _DocumentIngestion(), _Vision(), _DocumentRenderer()
    item_id = uuid4()
    repository = _Repository(status="uploading", items=[{
        "id": item_id, "ingestion_job_id": ingestion.job_id, "existing_ingestion_job_id": None,
        "source_relative_path": "方案/clubhouse.pptx", "source_file_name": "clubhouse.pptx",
        "source_file_type": "pptx", "source_sha256": "b" * 64, "source_size_bytes": 4096,
        "outcome": "archived", "error_code": None, "error_message": None,
        "processing_plan": {"processing_route": "mineru_vlm"},
        "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    }])
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor(), renderer)  # type: ignore[arg-type]

    result = service.prepare(repository.batch_id)

    assert result["status"] == "awaiting_approval"
    assert [task["task_kind"] for task in result["tasks"]] == ["mineru", "vision", "vision"]
    assert [task["page_start"] for task in result["tasks"] if task["task_kind"] == "vision"] == [1, 2]
    assert all(task["status"] == "awaiting_approval" for task in result["tasks"])
    assert len(ingestion.created_pages) == 2
    assert all(call["processing_profile"] == "vision" for call in ingestion.created_pages)
    assert ingestion.enqueue_calls == []
    assert vision.queue_calls == []
    page_plan = result["items"][0]["processing_plan"]["document_page_vision"]
    assert page_plan["page_count"] == 2
    assert page_plan["vision_task_count"] == 2
    assert page_plan["external_export_requires_explicit_approval"] is True


class _UploadRepository:
    def __init__(self, *, force_reprocess: bool) -> None:
        self.batch_id = str(uuid4())
        self.batch: dict[str, object] = {
            "id": self.batch_id,
            "project_external_key": "greentown-xiasha-01",
            "project_name": "01绿城下沙",
            "force_reprocess": force_reprocess,
            "status": "uploading",
        }
        self.items: list[dict[str, object]] = []
        self.existing = {"id": str(uuid4()), "status": "succeeded"}

    def get_batch(self, batch_id: str) -> dict[str, object] | None:
        return deepcopy(self.batch) if batch_id == self.batch_id else None

    def get_item_by_path(self, **_: object) -> None:
        return None

    def find_existing_ingestion_job(self, **_: object) -> dict[str, object]:
        return dict(self.existing)

    def add_item(self, **kwargs: object) -> dict[str, object]:
        item = {
            "id": str(uuid4()), "error_code": None, "error_message": None,
            "processing_plan": {}, "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 28, tzinfo=timezone.utc), **kwargs,
        }
        self.items.append(item)
        return item


class _UploadIngestion:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    def create_from_archived_file(self, **kwargs: object) -> dict[str, object]:
        self.create_calls.append(dict(kwargs))
        return {"id": str(uuid4()), "processing_route": "mineru_vlm", "route_reason": "test route"}


def test_upload_duplicate_is_skipped_by_default_but_force_reprocess_creates_a_new_audited_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This unit test only verifies duplicate/reimport routing. Keep the archive write
    # in memory so pytest does not depend on a user TEMP directory being writable.
    monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: BytesIO())

    duplicate_repository, duplicate_ingestion = _UploadRepository(force_reprocess=False), _UploadIngestion()
    duplicate_service = ImportBatchService(duplicate_repository, duplicate_ingestion, _Vision(), _Preprocessor())  # type: ignore[arg-type]
    duplicate = asyncio.run(duplicate_service.upload_file(
        batch_id=duplicate_repository.batch_id,
        file=UploadFile(filename="materials.pdf", file=BytesIO(b"same-source")),
        source_relative_path="materials/materials.pdf",
    ))

    assert duplicate["outcome"] == "duplicate_skipped"
    assert duplicate_ingestion.create_calls == []

    force_repository, force_ingestion = _UploadRepository(force_reprocess=True), _UploadIngestion()
    force_service = ImportBatchService(force_repository, force_ingestion, _Vision(), _Preprocessor())  # type: ignore[arg-type]
    reprocessed = asyncio.run(force_service.upload_file(
        batch_id=force_repository.batch_id,
        file=UploadFile(filename="materials.pdf", file=BytesIO(b"same-source")),
        source_relative_path="materials/materials.pdf",
    ))

    assert reprocessed["outcome"] == "archived"
    assert len(force_ingestion.create_calls) == 1
    call = force_ingestion.create_calls[0]
    assert "-reimport-" in str(call["document_external_key"])
    assert call["document_metadata"] == {
        "source": "admin-batch-import",
        "source_relative_path": "materials/materials.pdf",
        "batch_id": force_repository.batch_id,
        "reimport_of_ingestion_job_id": force_repository.existing["id"],
        "reimport_reason": "admin_batch_force_reprocess",
    }
    assert reprocessed["processing_plan"]["reprocess_of_ingestion_job_id"] == force_repository.existing["id"]


def test_prepare_oversized_document_skips_mineru_original_but_keeps_page_vision_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.import_batch_service import settings as import_settings

    monkeypatch.setattr(import_settings, "document_page_vision_enabled", True)
    monkeypatch.setattr(import_settings, "mineru_precision_max_file_bytes", 1024)
    ingestion, vision, renderer = _DocumentIngestion(), _Vision(), _DocumentRenderer()
    item_id = uuid4()
    repository = _Repository(status="uploading", items=[{
        "id": item_id, "ingestion_job_id": ingestion.job_id, "existing_ingestion_job_id": None,
        "source_relative_path": "方案/oversized.pptx", "source_file_name": "oversized.pptx",
        "source_file_type": "pptx", "source_sha256": "b" * 64, "source_size_bytes": 4096,
        "outcome": "archived", "error_code": None, "error_message": None,
        "processing_plan": {"processing_route": "mineru_vlm"},
        "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
    }])
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor(), renderer)  # type: ignore[arg-type]

    result = service.prepare(repository.batch_id)

    assert [task["task_kind"] for task in result["tasks"]] == ["vision", "vision"]
    plan = result["items"][0]["processing_plan"]
    assert plan["mineru"]["status"] == "not_planned"
    assert plan["document_page_vision"]["vision_task_count"] == 2





def test_prepare_pdf_creates_local_page_reviews_before_any_external_task() -> None:
    ingestion, vision, renderer = _DocumentIngestion(), _Vision(), _DocumentRenderer()
    item_id = uuid4()
    repository = _Repository(status="uploading", items=[{
        "id": item_id, "ingestion_job_id": ingestion.job_id, "existing_ingestion_job_id": None,
        "source_relative_path": "方案/clubhouse.pdf", "source_file_name": "clubhouse.pdf",
        "source_file_type": "pdf", "source_sha256": "b" * 64, "source_size_bytes": 4096,
        "outcome": "archived", "error_code": None, "error_message": None,
        "processing_plan": {"processing_route": "mineru_vlm"},
        "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
    }])
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor(), renderer)  # type: ignore[arg-type]

    result = service.prepare(repository.batch_id)

    assert result["status"] == "awaiting_page_review"
    assert result["tasks"] == []
    assert [review["source_page_number"] for review in result["page_reviews"]] == [1, 2]
    assert {review["selected_processor"] for review in result["page_reviews"]} == {"mineru"}
    assert result["items"][0]["processing_plan"]["pdf_page_review"]["page_count"] == 2


def test_nightly_approval_persists_schedule_without_external_submission() -> None:
    repository = _Repository(tasks=[_task("mineru", str(uuid4()))])
    ingestion, vision = _Ingestion(), _Vision()
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor())

    result = service.approve(
        batch_id=repository.batch_id, manifest_sha256="a" * 64, approve_mineru=True, approve_vision=False,
        schedule_mode="nightly",
    )

    assert ingestion.enqueue_calls == []
    assert vision.queue_calls == []
    assert result["status"] == "scheduled"
    assert result["schedule_mode"] == "nightly"
    assert result["scheduled_for"] is not None
    assert repository.tasks[0]["status"] == "scheduled"


def test_due_schedule_dispatches_only_previously_scheduled_tasks() -> None:
    repository = _Repository(tasks=[_task("mineru", str(uuid4()))])
    ingestion, vision = _Ingestion(), _Vision()
    service = ImportBatchService(repository, ingestion, vision, _Preprocessor())
    service.approve(
        batch_id=repository.batch_id, manifest_sha256="a" * 64, approve_mineru=True, approve_vision=False,
        schedule_mode="custom", scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    # Make the persisted schedule due without bypassing the future-time validation above.
    repository.batch["scheduled_for"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert service.dispatch_due_schedules() == 1
    assert ingestion.enqueue_calls == [repository.tasks[0]["ingestion_job_id"]]
    assert repository.batch["status"] == "queued"
    assert repository.tasks[0]["status"] == "queued"


def test_custom_schedule_requires_a_future_time() -> None:
    repository = _Repository(tasks=[_task("mineru", str(uuid4()))])
    service = ImportBatchService(repository, _Ingestion(), _Vision(), _Preprocessor())

    with pytest.raises(ImportBatchError, match="future"):
        service.approve(
            batch_id=repository.batch_id, manifest_sha256="a" * 64, approve_mineru=True, approve_vision=False,
            schedule_mode="custom", scheduled_for=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
