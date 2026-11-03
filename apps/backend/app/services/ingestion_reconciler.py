"""Background reconciliation for explicitly submitted MinerU ingestion jobs.

MinerU completes work asynchronously. This worker only polls jobs that a local user has
already explicitly submitted, then materializes completed parser results into the local
knowledge base. It never uploads files or submits new external work.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.ingestion_job_service import IngestionJobError, IngestionJobService

logger = logging.getLogger(__name__)


class IngestionReconciler:
    """Poll explicitly submitted MinerU jobs and materialize completed local results."""

    def __init__(self, ingestion_jobs: IngestionJobService) -> None:
        self.ingestion_jobs = ingestion_jobs

    async def run_forever(self) -> None:
        """Reconcile pending jobs until the local FastAPI application is stopped."""
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected ingestion reconciliation failure")
            await asyncio.sleep(settings.ingestion_reconcile_interval_seconds)

    def run_once(self) -> None:
        """Poll submitted jobs and materialize any successful unmaterialized result."""
        repository = self.ingestion_jobs.repository
        recovered = repository.recover_unsubmitted_ingestion_jobs()
        if recovered:
            logger.warning("Recovered %s interrupted local ingestion queue claim(s)", recovered)
        queued = repository.list_ingestion_jobs(
            limit=settings.ingestion_reconcile_batch_size,
            offset=0,
            project_id=None,
            status="queued",
        )["items"]
        submitted = repository.list_ingestion_jobs(
            limit=settings.ingestion_reconcile_batch_size,
            offset=0,
            project_id=None,
            status="submitted",
        )["items"]
        succeeded = repository.list_ingestion_jobs(
            limit=settings.ingestion_reconcile_batch_size,
            offset=0,
            project_id=None,
            status="succeeded",
        )["items"]

        seen: set[str] = set()
        for record in queued:
            job_id = str(record["id"])
            if job_id in seen:
                continue
            seen.add(job_id)
            try:
                self.ingestion_jobs.run_queued_submission(job_id)
            except IngestionJobError:
                logger.exception("Could not submit queued MinerU ingestion job %s", job_id)

        for record in [*submitted, *succeeded]:
            job_id = str(record["id"])
            if job_id in seen:
                continue
            seen.add(job_id)
            self._reconcile_job(job_id)

    def _reconcile_job(self, job_id: str) -> None:
        try:
            job = self.ingestion_jobs.get(job_id)
            if job["status"] == "submitted":
                job = self.ingestion_jobs.poll(job_id)
            if job["status"] == "succeeded" and not job.get("document_version_id"):
                self.ingestion_jobs.materialize(job_id)
                logger.info("Materialized completed MinerU ingestion job %s", job_id)
        except IngestionJobError:
            logger.exception("Could not reconcile ingestion job %s", job_id)
