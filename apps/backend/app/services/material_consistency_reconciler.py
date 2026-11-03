"""Background consumer for durable material-consistency audit jobs."""
from __future__ import annotations
import asyncio
import logging
from app.core.config import settings
from app.services.material_consistency_service import MaterialConsistencyService
logger = logging.getLogger(__name__)

class MaterialConsistencyReconciler:
    def __init__(self, service: MaterialConsistencyService) -> None:
        self.service = service
    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.service.run_next_queued)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected material consistency queue failure")
            await asyncio.sleep(settings.material_consistency_reconcile_interval_seconds)
