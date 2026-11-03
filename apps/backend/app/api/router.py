"""API router assembly."""

from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.assets import router as assets_router
from app.api.routes.agent import router as agent_router
from app.api.routes.answer import router as answer_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.ingestion_jobs import router as ingestion_jobs_router
from app.api.routes.material_consistency import router as material_consistency_router
from app.api.routes.import_batches import router as import_batches_router
from app.api.routes.rag_evaluations import router as rag_evaluations_router
from app.api.routes.search import router as search_router
from app.api.routes.visual_analysis_jobs import router as visual_analysis_jobs_router
from app.api.routes.harness import router as harness_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(agent_router)
api_router.include_router(assets_router)
api_router.include_router(documents_router)
api_router.include_router(ingestion_jobs_router)
api_router.include_router(search_router)
api_router.include_router(answer_router)
api_router.include_router(visual_analysis_jobs_router)
api_router.include_router(import_batches_router)
api_router.include_router(rag_evaluations_router)
api_router.include_router(material_consistency_router)
# Deliberately mounted only by app.main, never by app.remote_main.
api_router.include_router(admin_router)



api_router.include_router(harness_router)
