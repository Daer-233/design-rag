"""Main FastAPI application for design-rag."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import get_import_batch_service, get_ingestion_job_service, get_material_consistency_service, get_rag_evaluation_service, get_visual_analysis_service
from app.api.router import api_router
from app.core.config import settings
from app.services.ingestion_reconciler import IngestionReconciler
from app.services.import_batch_schedule_reconciler import ImportBatchScheduleReconciler
from app.services.import_batch_hybrid_reconciler import ImportBatchHybridReconciler
from app.services.material_consistency_reconciler import MaterialConsistencyReconciler
from app.services.rag_evaluation_reconciler import RagEvaluationReconciler
from app.services.visual_analysis_reconciler import VisualAnalysisReconciler
from app.repositories.harness_repository import HarnessRepository
from app.services.image_generation_reconciler import ImageGenerationReconciler
from app.services.harness_reconciler import HarnessReconciler


def _has_valid_admin_web_build(directory: Path) -> bool:
    """Return True only for a readable, normal HTML Vite entry point.

    This prevents the local API from serving an incomplete or corrupted static build.
    """
    index_path = directory / "index.html"
    if not directory.is_dir() or not index_path.is_file():
        return False

    try:
        leading_content = index_path.read_bytes()[:512].lstrip().lower()
    except OSError:
        return False

    return leading_content.startswith(b"<!doctype html") or leading_content.startswith(b"<html")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Keep submitted MinerU jobs moving without blocking the local Admin API."""
    reconciliation_tasks = [
        asyncio.create_task(
            IngestionReconciler(get_ingestion_job_service()).run_forever(),
            name="ingestion-reconciler",
        ),
        asyncio.create_task(
            VisualAnalysisReconciler(get_visual_analysis_service()).run_forever(),
            name="visual-analysis-reconciler",
        ),
        asyncio.create_task(
            ImportBatchHybridReconciler(
                get_import_batch_service().repository,
                get_ingestion_job_service(),
                get_visual_analysis_service(),
            ).run_forever(),
            name="import-batch-hybrid-reconciler",
        ),
        asyncio.create_task(
            ImportBatchScheduleReconciler(get_import_batch_service()).run_forever(),
            name="import-batch-schedule-reconciler",
        ),
        asyncio.create_task(
            RagEvaluationReconciler(get_rag_evaluation_service()).run_forever(),
            name="rag-evaluation-reconciler",
        ),
        asyncio.create_task(
            MaterialConsistencyReconciler(get_material_consistency_service()).run_forever(),
            name="material-consistency-reconciler",
        ),
        asyncio.create_task(
            ImageGenerationReconciler(HarnessRepository()).run_forever(),
            name="image-generation-reconciler",
        ),
        asyncio.create_task(
            HarnessReconciler().run_forever(),
            name="harness-reconciler",
        ),
    ]
    try:
        yield
    finally:
        for task in reconciliation_tasks:
            task.cancel()
        for task in reconciliation_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Design case RAG system backend",
    lifespan=lifespan,
)
app.include_router(api_router)

# Local SPAs are served only by the full local backend. remote_main intentionally
# omits them together with administration, ingestion and anonymous Agent routes.
admin_web_dist = Path(__file__).resolve().parents[2] / "admin-web" / "dist"
chat_web_dist = Path(__file__).resolve().parents[2] / "chat-web" / "dist"
admin_web_available = _has_valid_admin_web_build(admin_web_dist)
chat_web_available = _has_valid_admin_web_build(chat_web_dist)
if admin_web_available:
    app.mount("/admin", StaticFiles(directory=admin_web_dist, html=True), name="admin-web")
if chat_web_available:
    app.mount("/chat", StaticFiles(directory=chat_web_dist, html=True), name="chat-web")


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "docs": "/docs",
        "admin": "/admin/" if admin_web_available else "admin web build not found or invalid",
        "chat": "/chat/" if chat_web_available else "chat web build not found or invalid",
    }
