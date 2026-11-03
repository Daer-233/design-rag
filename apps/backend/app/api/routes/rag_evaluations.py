"""Local Admin API for durable, explicitly approved RAGAS quality evaluations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_evaluation_repository, get_rag_evaluation_service
from app.api.schemas import (
    RagEvaluationCaseCreateRequest,
    RagEvaluationCaseUpdateRequest,
    RagEvaluationRunCreateRequest,
    RagEvaluationSuiteCreateRequest,
    RagEvaluationSuiteUpdateRequest,
)
from app.repositories.evaluation_repository import EvaluationRepository
from app.services.rag_evaluation_service import RagEvaluationError, RagEvaluationService

router = APIRouter(prefix="/api/v1/admin/rag-evaluations", tags=["rag-quality-evaluation"])


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{kind} not found")


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@router.get("/capabilities")
def get_capabilities(
    service: Annotated[RagEvaluationService, Depends(get_rag_evaluation_service)],
) -> dict[str, object]:
    """Return evaluator availability without exposing gateway credentials."""
    return service.capabilities()


@router.get("/suites")
def list_suites(
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
    project_id: UUID | None = None,
) -> dict[str, object]:
    return {"items": repository.list_suites(project_id=str(project_id) if project_id else None)}


@router.post("/suites", status_code=status.HTTP_201_CREATED)
def create_suite(
    payload: RagEvaluationSuiteCreateRequest,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    try:
        return repository.create_suite(
            project_id=str(payload.project_id), name=payload.name.strip(), description=payload.description.strip()
        )
    except ValueError as error:
        raise _unprocessable(error) from error


@router.get("/suites/{suite_id}")
def get_suite(
    suite_id: UUID,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    suite = repository.get_suite(str(suite_id))
    if not suite:
        raise _not_found("Evaluation suite")
    return suite


@router.patch("/suites/{suite_id}")
def update_suite(
    suite_id: UUID,
    payload: RagEvaluationSuiteUpdateRequest,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    suite = repository.update_suite(
        suite_id=str(suite_id), name=payload.name.strip(), description=payload.description.strip()
    )
    if not suite:
        raise _not_found("Evaluation suite")
    return suite


@router.post("/suites/{suite_id}/cases", status_code=status.HTTP_201_CREATED)
def create_case(
    suite_id: UUID,
    payload: RagEvaluationCaseCreateRequest,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    if not repository.get_suite(str(suite_id)):
        raise _not_found("Evaluation suite")
    try:
        return repository.create_case(
            suite_id=str(suite_id),
            question=payload.question.strip(),
            reference_answer=payload.reference_answer.strip(),
            enabled=payload.enabled,
            sort_order=payload.sort_order,
        )
    except ValueError as error:
        raise _unprocessable(error) from error


@router.patch("/cases/{case_id}")
def update_case(
    case_id: UUID,
    payload: RagEvaluationCaseUpdateRequest,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    case = repository.update_case(
        case_id=str(case_id),
        question=payload.question.strip(),
        reference_answer=payload.reference_answer.strip(),
        enabled=payload.enabled,
        sort_order=payload.sort_order,
    )
    if not case:
        raise _not_found("Evaluation case")
    return case


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(
    case_id: UUID,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> None:
    if not repository.delete_case(str(case_id)):
        raise _not_found("Evaluation case")


@router.get("/runs")
def list_runs(
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
    suite_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    return {"items": repository.list_runs(suite_id=str(suite_id) if suite_id else None, limit=limit)}


@router.get("/runs/{run_id}")
def get_run(
    run_id: UUID,
    repository: Annotated[EvaluationRepository, Depends(get_evaluation_repository)],
) -> dict[str, object]:
    run = repository.get_run(str(run_id))
    if not run:
        raise _not_found("Evaluation run")
    return run


@router.post("/suites/{suite_id}/runs", status_code=status.HTTP_201_CREATED)
def queue_run(
    suite_id: UUID,
    payload: RagEvaluationRunCreateRequest,
    service: Annotated[RagEvaluationService, Depends(get_rag_evaluation_service)],
) -> dict[str, object]:
    # Literal[True] in the request schema is intentional: RAGAS sends only the
    # selected test inputs and already-retrieved snippets to the configured gateway.
    try:
        return service.queue_run(suite_id=str(suite_id), evidence_top_k=payload.evidence_top_k)
    except RagEvaluationError as error:
        if str(error) == "Evaluation suite not found":
            raise _not_found("Evaluation suite") from error
        raise _unprocessable(error) from error
    except ValueError as error:
        raise _unprocessable(error) from error
