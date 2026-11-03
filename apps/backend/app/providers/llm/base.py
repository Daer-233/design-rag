"""LLM provider contracts. The backend depends on these contracts, not a gateway SDK."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class VisionProviderError(RuntimeError):
    pass


class AnswerProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisionAnalysis:
    analysis: dict[str, object]
    provider_response: dict[str, object]


@dataclass(frozen=True)
class GroundedAnswer:
    answer: str
    citations: list[str]
    insufficient_evidence: bool
    limitations: list[str]
    provider_response: dict[str, object]


@dataclass(frozen=True)
class MaterialConsistencyJudgement:
    """Conservative text-only comparison of a structured visual fact and a supplied material snippet."""
    verdict: str
    confidence: float
    reason: str
    limitations: list[str]
    provider_response: dict[str, object]

    def safe_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict, "confidence": self.confidence, "reason": self.reason,
            "limitations": self.limitations, "provider_response": self.provider_response,
        }


class VisionProvider(Protocol):
    provider_name: str

    def is_configured(self) -> bool: ...

    def analyze_image(
        self,
        source_path: Path,
        *,
        source_sha256: str,
        prompt_version: str,
    ) -> VisionAnalysis: ...


class AnswerProvider(Protocol):
    provider_name: str

    def is_configured(self) -> bool: ...

    def answer_with_evidence(
        self,
        *,
        question: str,
        evidence_packet: str,
        allowed_citation_ids: list[str],
        prompt_version: str,
    ) -> GroundedAnswer: ...

    def compare_material_consistency(
        self, *, visual_material: dict[str, object], material_evidence: dict[str, object],
        prompt_version: str,
    ) -> MaterialConsistencyJudgement: ...
