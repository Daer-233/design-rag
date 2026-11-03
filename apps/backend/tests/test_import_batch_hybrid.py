from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.services.import_batch_hybrid_reconciler import ImportBatchHybridReconciler


class FakeImportRepository:
    def __init__(self, batch_id: str, item_id: str, parent_id: str, *, vision_approved: bool) -> None:
        self.batch_id = batch_id
        self.item_id = item_id
        self.parent_id = parent_id
        self.batch = {
            "id": batch_id,
            "project_external_key": "demo",
            "project_name": "Demo",
            "vision_approved": vision_approved,
        }
        self.parent = {
            "id": parent_id,
            "item_id": item_id,
            "task_kind": "mineru",
            "ingestion_job_id": "mineru-job",
            "status": "succeeded",
            "details": {
                "pipeline": "mineru_then_gpt",
                "hybrid_source_page_numbers": [2],
            },
        }
        self.children: list[dict[str, object]] = []
        self.updated: list[tuple[str, str]] = []

    def list_batches(self, **_: object) -> dict[str, object]:
        return {"items": [self.batch], "total": 1}

    def list_tasks(self, _batch_id: str) -> list[dict[str, object]]:
        return [self.parent, *self.children]

    def list_items(self, _batch_id: str) -> list[dict[str, object]]:
        return [{"id": self.item_id, "source_file_name": "interior.pdf", "source_sha256": "a" * 64}]

    def find_task_by_detail(self, *, key: str, value: str, **_: object) -> dict[str, object] | None:
        return next((task for task in self.children if task["details"].get(key) == value), None)

    def add_task(self, **kwargs: object) -> dict[str, object]:
        task = {"id": str(uuid4()), "status": "awaiting_approval", **kwargs}
        self.children.append(task)
        return task

    def update_task(self, task_id: str, *, status: str, **_: object) -> None:
        self.updated.append((task_id, status))

    def set_status(self, *_: object, **__: object) -> None:
        return None


class FakeRagRepository:
    def __init__(self, asset: dict[str, object]) -> None:
        self.asset = asset

    def list_mineru_image_assets(self, _job_id: str, *, source_pages: list[int] | None = None) -> list[dict[str, object]]:
        assert source_pages == [2]
        return [self.asset]


class FakeIngestion:
    def __init__(self, path: Path, rag_repository: FakeRagRepository) -> None:
        self.repository = rag_repository
        self.path = path
        self.created: list[dict[str, object]] = []

    def get(self, _job_id: str) -> dict[str, object]:
        return {"status": "succeeded", "document_version_id": str(uuid4())}

    def create_from_archived_file(self, **kwargs: object) -> dict[str, object]:
        self.created.append(kwargs)
        return {"id": str(uuid4())}


class FakeVision:
    def __init__(self) -> None:
        self.queued: list[str] = []

    def queue_batch(self, ids: list[str]) -> list[dict[str, object]]:
        self.queued.extend(ids)
        return [{"outcome": "queued", "job": {"id": str(uuid4())}}]


def make_reconciler(tmp_path: Path, *, vision_approved: bool):
    monkey_path = tmp_path / "documents" / "derived" / "mineru-image.jpg"
    monkey_path.parent.mkdir(parents=True)
    monkey_path.write_bytes(b"mineru extracted image")
    asset = {
        "id": str(uuid4()),
        "storage_path": str(monkey_path),
        "content_type": "image/jpeg",
        "page_number": 2,
        "bbox": {"x": 1},
        "content_sha256": "not-a-file-sha",
        "size_bytes": 1,
        "file_name": monkey_path.name,
        "adjacent_text": "沙发",
        "caption_text": "效果图",
    }
    batch_id, item_id, parent_id = str(uuid4()), str(uuid4()), str(uuid4())
    repo = FakeImportRepository(batch_id, item_id, parent_id, vision_approved=vision_approved)
    ingestion = FakeIngestion(monkey_path, FakeRagRepository(asset))
    visual = FakeVision()
    reconciler = ImportBatchHybridReconciler(repo, ingestion, visual)  # type: ignore[arg-type]
    return reconciler, repo, ingestion, visual


def test_hybrid_creates_one_gpt_job_from_mineru_image_and_uses_file_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", tmp_path)
    reconciler, repo, ingestion, visual = make_reconciler(tmp_path, vision_approved=True)

    assert reconciler.run_once() == 1
    assert len(ingestion.created) == 1
    assert ingestion.created[0]["source_sha256"] != "not-a-file-sha"
    assert len(repo.children) == 1
    assert len(visual.queued) == 1
    assert repo.children[0]["details"]["source_page_number"] == 2


def test_hybrid_keeps_gpt_child_waiting_without_vision_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", tmp_path)
    reconciler, repo, _ingestion, visual = make_reconciler(tmp_path, vision_approved=False)

    assert reconciler.run_once() == 1
    assert repo.children[0]["status"] == "awaiting_approval"
    assert visual.queued == []


def test_hybrid_reconciliation_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", tmp_path)
    reconciler, repo, ingestion, visual = make_reconciler(tmp_path, vision_approved=True)

    reconciler.run_once()
    assert reconciler.run_once() == 0
    assert len(repo.children) == 1
    assert len(ingestion.created) == 1
    assert len(visual.queued) == 1

