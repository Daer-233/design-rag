"""Authoritative document ingestion endpoint."""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_ingestion_service
from app.api.schemas import IngestRequest

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
def ingest_document(payload: IngestRequest) -> object:
    try:
        result = get_ingestion_service().ingest(payload.model_dump(mode="json"))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store authoritative document data: {type(error).__name__}",
        ) from error
    if result["index_status"] != "indexed":
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result)
    return result
