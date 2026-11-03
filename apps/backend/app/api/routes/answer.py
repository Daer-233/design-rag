"""Evidence-constrained answer endpoint.

The caller must explicitly acknowledge the gateway export on every request. Only the
question and the selected text evidence are submitted; no original source file is read
or exported by this route.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import get_answer_service
from app.api.schemas import AnswerRequest
from app.clients.inference import InferenceServiceError
from app.core.config import settings
from app.providers.llm.base import AnswerProviderError
from app.services.access_policy import AccessDeniedError

router = APIRouter(prefix="/api/v1", tags=["generation"])


@router.post("/answer")
def answer(payload: AnswerRequest) -> dict[str, object]:
    # The Agent-facing surface is evidence-only. Keeping this endpoint disabled by
    # default prevents a bearer-token Agent from turning a retrieval call into an
    # external GPT request. It must be deliberately enabled in a future local-only
    # generation workflow, with separate authorization added at that time.
    if not settings.answer_api_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Answer API is disabled in agent-retrieval mode",
        )
    try:
        return get_answer_service().answer(
            query=payload.query,
            project_id=str(payload.project_id),
            principal_raw=payload.principal,
            evidence_top_k=payload.evidence_top_k,
        )
    except AccessDeniedError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except (InferenceServiceError, AnswerProviderError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Answer generation failed: {type(error).__name__}",
        ) from error
