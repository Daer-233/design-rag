"""Background consumer for explicitly approved RAGAS quality-evaluation runs."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.rag_evaluation_service import RagEvaluationService

logger = logging.getLogger(__name__)


class RagEvaluationReconciler:
    """Consumes only locally approved evaluation runs, one durable run at a time."""

    def __init__(self, service: RagEvaluationService) -> None:
        self.service = service

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.service.run_next_queued)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected RAGAS evaluation queue failure")
            await asyncio.sleep(settings.rag_evaluation_reconcile_interval_seconds)
