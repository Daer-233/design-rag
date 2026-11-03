"""API router assembly."""

from fastapi import APIRouter

from embedding_app.api.routes.embedding import router as embedding_router

api_router = APIRouter()
api_router.include_router(embedding_router)
