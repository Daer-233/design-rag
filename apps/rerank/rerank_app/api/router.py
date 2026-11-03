from fastapi import APIRouter

from rerank_app.api.routes.rerank import router as rerank_router

api_router = APIRouter()
api_router.include_router(rerank_router)
