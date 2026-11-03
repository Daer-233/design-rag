"""Durable RAGAS evaluation orchestration for the local Admin workspace."""

from __future__ import annotations

from collections import defaultdict
from time import perf_counter
from typing import Any

from app.core.config import settings
from app.providers.llm.base import AnswerProvider
from app.repositories.evaluation_repository import EvaluationRepository
from app.services.access_policy import Principal
from app.services.answer_service import AnswerService
from app.services.rag_service import SearchService


class RagEvaluationError(RuntimeError):
    pass


class RagEvaluationService:
    """Runs explicitly approved, bounded quality suites one case at a time.

    The service evaluates the same retrieval + evidence-constrained answer path used by
    the application. RAGAS only receives the test question, generated answer, reference
    answer and already-selected retrieval snippets; it never receives project archives.
    """

    MAX_ENABLED_CASES = 30
    METRIC_NAMES = ("faithfulness", "context_precision", "context_recall")

    def __init__(self, repository: EvaluationRepository, search_service: SearchService,
                 answer_provider: AnswerProvider) -> None:
        self.repository = repository
        self.search_service = search_service
        self.answer_provider = answer_provider

    def capabilities(self) -> dict[str, object]:
        version, available = self._ragas_version()
        return {
            "evaluator": "ragas",
            "available": available,
            "version": version,
            "metrics": list(self.METRIC_NAMES),
            "model_configured": self.answer_provider.is_configured(),
            "max_cases_per_run": self._max_enabled_cases(),
            "external_submission": {
                "required": True,
                "included": ["test_question", "reference_answer", "generated_answer", "retrieved_snippets"],
                "excluded": ["original_files", "archive_paths", "credentials", "unretrieved_corpus"],
            },
        }

    def queue_run(self, *, suite_id: str, evidence_top_k: int) -> dict[str, object]:
        suite = self.repository.get_suite(suite_id)
        if not suite:
            raise RagEvaluationError("Evaluation suite not found")
        cases = [item for item in suite.get("cases", []) if isinstance(item, dict) and item.get("enabled")]
        if not cases:
            raise RagEvaluationError("Add at least one enabled evaluation case before running")
        if len(cases) > self._max_enabled_cases():
            raise RagEvaluationError(f"An evaluation run supports at most {self._max_enabled_cases()} enabled cases")
        if not self.answer_provider.is_configured():
            raise RagEvaluationError("LLM gateway is not configured. Configure LLM_API_KEY before running RAGAS evaluation.")
        version, available = self._ragas_version()
        if not available:
            raise RagEvaluationError("RAGAS is not available in the backend environment. Install the configured dependency first.")
        return self.repository.create_run(
            suite_id=suite_id,
            project_id=str(suite["project_id"]),
            model_id=settings.llm_model_id,
            evidence_top_k=evidence_top_k,
            evaluator_version=version,
            approval_record={
                "explicit_external_evaluation_approved": True,
                "included": ["test_question", "reference_answer", "generated_answer", "retrieved_snippets"],
                "excluded": ["original_files", "archive_paths", "credentials", "unretrieved_corpus"],
            },
        )

    def run_next_queued(self) -> dict[str, object] | None:
        run = self.repository.claim_next_run()
        if not run:
            return None
        run_id = str(run["id"])
        try:
            cases = self.repository.list_enabled_cases(str(run["suite_id"]))
            if not cases:
                raise RagEvaluationError("The suite has no enabled cases")
            if len(cases) > self._max_enabled_cases():
                raise RagEvaluationError(f"Suite exceeds the {self._max_enabled_cases()}-case run limit")
            llm = self._build_ragas_llm()
            completed, failed, score_values = 0, 0, defaultdict(list)
            for case in cases:
                try:
                    started = perf_counter()
                    result = self._evaluate_case(
                        case=case,
                        project_id=str(run["project_id"]),
                        evidence_top_k=int(run["evidence_top_k"]),
                        ragas_llm=llm,
                    )
                    duration_ms = round((perf_counter() - started) * 1000, 2)
                    self.repository.store_case_result(
                        run_id=run_id,
                        case_id=str(case["id"]),
                        status="succeeded",
                        generated_answer=result["generated_answer"],
                        retrieved_contexts=result["retrieved_contexts"],
                        citations=result["citations"],
                        scores=result["scores"],
                        error_code=None,
                        error_message=None,
                        duration_ms=duration_ms,
                    )
                    completed += 1
                    for key, value in result["scores"].items():
                        if isinstance(value, (float, int)):
                            score_values[key].append(float(value))
                except Exception as error:  # one bad evaluation item must not discard the complete run
                    failed += 1
                    self.repository.store_case_result(
                        run_id=run_id,
                        case_id=str(case["id"]),
                        status="failed",
                        generated_answer=None,
                        retrieved_contexts=[],
                        citations=[],
                        scores={},
                        error_code=type(error).__name__,
                        error_message=str(error)[:1200],
                        duration_ms=None,
                    )
            summary = {
                "case_count": len(cases),
                "succeeded_case_count": completed,
                "failed_case_count": failed,
                "scores": {key: round(sum(values) / len(values), 4) for key, values in score_values.items() if values},
            }
            status = "succeeded" if failed == 0 else "partially_succeeded" if completed else "failed"
            self.repository.finish_run(run_id=run_id, status=status, summary=summary)
        except Exception as error:
            self.repository.finish_run(
                run_id=run_id,
                status="failed",
                summary={"case_count": 0, "succeeded_case_count": 0, "failed_case_count": 0, "scores": {}},
                error_code=type(error).__name__,
                error_message=str(error)[:1200],
            )
        return self.repository.get_run(run_id)

    def _evaluate_case(self, *, case: dict[str, object], project_id: str, evidence_top_k: int,
                       ragas_llm: object) -> dict[str, object]:
        query = str(case["question"])
        reference = str(case["reference_answer"])
        principal = Principal(user_id="local-ragas-evaluator", project_ids={project_id}, roles=set(), all_projects=True)
        retrieval = self.search_service.search(
            query=query,
            project_id=project_id,
            principal_raw=principal,
            evidence_top_k=evidence_top_k,
        )
        evidence = retrieval.get("results") if isinstance(retrieval.get("results"), list) else []
        contexts = [self._context_from_evidence(item) for item in evidence if isinstance(item, dict)]
        contexts = [item for item in contexts if item["content"]]
        if not contexts:
            raise RagEvaluationError("No retrieval evidence was found for this evaluation case")
        packet, source_map, _ = AnswerService._evidence_packet(evidence)
        generated = self.answer_provider.answer_with_evidence(
            question=query,
            evidence_packet=packet,
            allowed_citation_ids=list(source_map),
            prompt_version=settings.answer_prompt_version,
        )
        scores = self._score_with_ragas(
            ragas_llm=ragas_llm,
            query=query,
            generated_answer=generated.answer,
            reference_answer=reference,
            contexts=[str(item["content"]) for item in contexts],
        )
        citations = [
            {"source_id": source_id, **source_map[source_id]}
            for source_id in generated.citations
            if source_id in source_map
        ]
        return {
            "generated_answer": generated.answer,
            "retrieved_contexts": contexts,
            "citations": citations,
            "scores": scores,
        }

    @staticmethod
    def _max_enabled_cases() -> int:
        return int(getattr(settings, "rag_evaluation_max_cases_per_run", RagEvaluationService.MAX_ENABLED_CASES))

    @staticmethod
    def _context_from_evidence(item: dict[str, object]) -> dict[str, object]:
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        content = str(item.get("content") or "").strip()
        if len(content) > 6_000:
            content = content[:6_000].rstrip() + "\n[评测上下文已截断]"
        return {
            "chunk_id": item.get("chunk_id"),
            "content": content,
            "citation": citation,
            "evidence_type": item.get("evidence_type"),
            "scores": item.get("scores") if isinstance(item.get("scores"), dict) else {},
        }

    def _build_ragas_llm(self) -> object:
        api_key = settings.effective_llm_api_key
        if not api_key:
            raise RagEvaluationError("LLM gateway is not configured")
        try:
            from openai import OpenAI
            from ragas.llms import llm_factory
        except ImportError as error:
            raise RagEvaluationError("RAGAS dependencies are not installed") from error
        client = OpenAI(api_key=api_key, base_url=settings.llm_base_url.rstrip("/"))
        return llm_factory(settings.llm_model_id, provider="openai", client=client)

    @staticmethod
    def _score_with_ragas(*, ragas_llm: object, query: str, generated_answer: str, reference_answer: str,
                          contexts: list[str]) -> dict[str, float]:
        try:
            from ragas.dataset_schema import SingleTurnSample
            from ragas.metrics import ContextPrecision, ContextRecall, Faithfulness
        except ImportError as error:
            raise RagEvaluationError("RAGAS dependencies are not installed") from error
        sample = SingleTurnSample(
            user_input=query,
            response=generated_answer,
            reference=reference_answer,
            retrieved_contexts=contexts,
        )
        metrics = (
            Faithfulness(llm=ragas_llm),
            ContextPrecision(llm=ragas_llm),
            ContextRecall(llm=ragas_llm),
        )
        scores: dict[str, float] = {}
        for metric in metrics:
            value = metric.single_turn_score(sample)
            if value is not None:
                scores[str(metric.name)] = round(float(value), 4)
        return scores

    @staticmethod
    def _ragas_version() -> tuple[str | None, bool]:
        try:
            import ragas
            return str(getattr(ragas, "__version__", "unknown")), True
        except ImportError:
            return None, False
