"""Background consumer for explicitly approved visual-analysis queue entries."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.visual_analysis_service import VisualAnalysisService

logger = logging.getLogger(__name__)


class VisualAnalysisReconciler:
    """Run local queued image tasks one at a time; never discover or export new work."""

    def __init__(self, visual_analysis: VisualAnalysisService) -> None:
        self.visual_analysis = visual_analysis

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected visual-analysis queue failure")
            await asyncio.sleep(settings.visual_analysis_reconcile_interval_seconds)

    def run_once(self) -> None:
        # A cancellation requested during a prior process lifetime cannot reach the
        # normal post-provider boundary.  Finalize it before claiming any new work.
        self.visual_analysis.repository.recover_cancel_requested_visual_analysis_jobs()
        # A service restart cannot resume a Python call that was in progress.  Repair
        # only stale durable state: materialized results become succeeded; records
        # without a materialized result become an explicit failure for manual retry.
        # This path never re-exports an image to the external provider.
        self.visual_analysis.repository.recover_stale_running_visual_analysis_jobs(
            stale_after_seconds=settings.visual_analysis_stale_running_seconds
        )
        job_ids = self.visual_analysis.repository.list_queued_visual_analysis_jobs(
            limit=settings.visual_analysis_reconcile_batch_size
        )
        for job_id in job_ids:
            try:
                self.visual_analysis.run_queued_job(job_id)
            except Exception:
                # The service persists a per-job failure reason and applies at most one
                # retry; keep the worker alive for unrelated images.
                logger.exception("Could not process queued visual-analysis job %s", job_id)
