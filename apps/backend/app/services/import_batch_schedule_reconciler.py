"""Durable dispatcher for explicitly approved scheduled import batches."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.import_batch_service import ImportBatchService

logger = logging.getLogger(__name__)


class ImportBatchScheduleReconciler:
    """Checks persistent schedules; it never creates or approves new external work."""

    def __init__(self, service: ImportBatchService) -> None:
        self.service = service

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.service.dispatch_due_schedules)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected scheduled import batch dispatch failure")
            await asyncio.sleep(settings.import_batch_schedule_reconcile_interval_seconds)
