from app.services.ingestion_reconciler import IngestionReconciler
from app.services.visual_analysis_reconciler import VisualAnalysisReconciler


class FakeRepository:
    def __init__(self) -> None:
        self.records = {
            "queued": [{"id": "queued-job"}],
            "submitted": [{"id": "submitted-job"}],
            "succeeded": [{"id": "completed-job"}],
        }

    def list_ingestion_jobs(self, *, status: str, **_: object) -> dict[str, object]:
        return {"items": self.records[status], "total": len(self.records[status])}

    def recover_unsubmitted_ingestion_jobs(self) -> int:
        return 1


class FakeIngestionJobs:
    def __init__(self) -> None:
        self.repository = FakeRepository()
        self.jobs = {
            "queued-job": {"id": "queued-job", "status": "queued", "document_version_id": None},
            "submitted-job": {"id": "submitted-job", "status": "submitted", "document_version_id": None},
            "completed-job": {"id": "completed-job", "status": "succeeded", "document_version_id": None},
        }
        self.polled: list[str] = []
        self.materialized: list[str] = []
        self.queued_submissions: list[str] = []

    def run_queued_submission(self, job_id: str) -> dict[str, object]:
        self.queued_submissions.append(job_id)
        self.jobs[job_id]["status"] = "submitted"
        return self.jobs[job_id]

    def get(self, job_id: str) -> dict[str, object]:
        return self.jobs[job_id]

    def poll(self, job_id: str) -> dict[str, object]:
        self.polled.append(job_id)
        self.jobs[job_id]["status"] = "succeeded"
        return self.jobs[job_id]

    def materialize(self, job_id: str) -> dict[str, object]:
        self.materialized.append(job_id)
        self.jobs[job_id]["document_version_id"] = f"version-{job_id}"
        return {"job": self.jobs[job_id], "materialized": True}


def test_reconciler_polls_submitted_and_materializes_completed_jobs() -> None:
    jobs = FakeIngestionJobs()

    IngestionReconciler(jobs).run_once()

    assert jobs.queued_submissions == ["queued-job"]
    assert jobs.polled == ["submitted-job"]
    assert jobs.materialized == ["submitted-job", "completed-job"]


class FakeVisualQueueRepository:
    def __init__(self) -> None:
        self.recovery_calls = 0
        self.stale_recovery_calls: list[int] = []
        self.queued_lookups = 0

    def recover_cancel_requested_visual_analysis_jobs(self) -> int:
        self.recovery_calls += 1
        return 1

    def recover_stale_running_visual_analysis_jobs(self, *, stale_after_seconds: int) -> dict[str, int]:
        self.stale_recovery_calls.append(stale_after_seconds)
        return {"restored": 1, "failed": 0, "cancelled": 0}

    def list_queued_visual_analysis_jobs(self, *, limit: int) -> list[str]:
        self.queued_lookups += 1
        assert limit > 0
        return ["queued-image"]


class FakeVisualQueueService:
    def __init__(self) -> None:
        self.repository = FakeVisualQueueRepository()
        self.ran: list[str] = []

    def run_queued_job(self, job_id: str) -> None:
        self.ran.append(job_id)


def test_visual_reconciler_finalizes_cancelled_jobs_before_claiming_queue() -> None:
    service = FakeVisualQueueService()

    VisualAnalysisReconciler(service).run_once()  # type: ignore[arg-type]

    assert service.repository.recovery_calls == 1
    assert service.repository.stale_recovery_calls == [3600]
    assert service.repository.queued_lookups == 1
    assert service.ran == ["queued-image"]
