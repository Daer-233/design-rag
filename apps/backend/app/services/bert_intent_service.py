"""CPU-local BERT NLI classifier for the initial professional/daily routing split."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class IntentResult:
    label: str
    confidence: float
    professional_score: float
    daily_score: float
    classifier: str
    fallback_reason: str | None = None


class BertIntentClassifier:
    """Classify a Chinese question with a local RoBERTa/BERT NLI checkpoint.

    The NLI formulation avoids pretending that an unfine-tuned base BERT is a
    domain classifier: the question is paired with two explicit hypotheses and
    their entailment confidence determines the temporary two-way route.
    """

    _professional_hypothesis = "这是一条需要查询室内设计项目知识库、设计资料或图纸的专业问题。"
    _daily_hypothesis = "这是一条日常聊天、泛知识或互联网问题，不需要查询室内设计项目知识库。"
    _domain_terms = (
        "设计", "方案", "项目", "材料", "材质", "石材", "木饰面", "墙布", "软装", "家具", "灯具",
        "图纸", "平面", "立面", "剖面", "节点", "尺寸", "标高", "色彩", "风格", "空间", "会所",
        "下沙", "中海", "华润", "海底捞", "静安", "ppt", "pdf", "图例", "主材", "品牌", "型号",
    )
    # Explicit everyday requests are a calibration guard for the generic NLI checkpoint.
    # They never override a query that actually includes a design-domain term.
    _daily_terms = (
        "天气", "气温", "下雨", "新闻", "股票", "汇率", "食谱", "电影", "音乐", "笑话",
        "你好", "早上好", "晚上好", "晚安", "谢谢", "再见",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._load_error: str | None = None

    def classify(self, question: str) -> IntentResult:
        text = question.strip()
        if not text:
            return IntentResult("daily", 0.5, 0.5, 0.5, "bert-nli", "empty_question")
        try:
            tokenizer, model = self._ensure_model()
            return self._classify_with_model(tokenizer, model, text)
        except Exception as error:  # Local fallback keeps the chat usable while model setup is repaired.
            return self._fallback(text, f"{type(error).__name__}: {error}")

    def _ensure_model(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        with self._lock:
            if self._tokenizer is not None and self._model is not None:
                return self._tokenizer, self._model
            from modelscope.hub.snapshot_download import snapshot_download
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            model_dir = snapshot_download(
                settings.agent_intent_model_id,
                revision=settings.agent_intent_model_revision,
                local_files_only=settings.agent_intent_local_files_only,
            )
            tokenizer = AutoTokenizer.from_pretrained(model_dir)
            model = AutoModelForSequenceClassification.from_pretrained(model_dir)
            model.eval()
            self._tokenizer, self._model, self._load_error = tokenizer, model, None
            return tokenizer, model

    @staticmethod
    def _entailment_index(model: Any) -> int:
        labels = getattr(getattr(model, "config", None), "id2label", {}) or {}
        for raw_index, raw_label in labels.items():
            if "entail" in str(raw_label).lower() or "蕴含" in str(raw_label):
                return int(raw_index)
        # The selected NLI checkpoint uses entailment as its first class. This is
        # intentionally only the documented fallback when a config omits labels.
        return 0

    def _classify_with_model(self, tokenizer: Any, model: Any, question: str) -> IntentResult:
        import torch

        encoded = tokenizer(
            [question, question],
            [self._professional_hypothesis, self._daily_hypothesis],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=settings.agent_intent_max_length,
        )
        with torch.inference_mode():
            logits = model(**encoded).logits
            probabilities = torch.softmax(logits, dim=-1)
        entailment = self._entailment_index(model)
        professional_score = float(probabilities[0][entailment].item())
        daily_score = float(probabilities[1][entailment].item())
        total = professional_score + daily_score
        normalized_professional = professional_score / total if total else 0.5
        normalized_daily = daily_score / total if total else 0.5
        domain_hits = sum(term in question.lower() for term in self._domain_terms)
        daily_hits = sum(term in question.lower() for term in self._daily_terms)
        classifier = "bert-nli"

        # NLI judges semantic entailment, not a purpose-built intent taxonomy. Keep
        # BERT as the classifier, then apply only deterministic local calibration for
        # obvious daily wording and named design knowledge-base terminology.
        if daily_hits and not domain_hits:
            normalized_professional, normalized_daily = 0.08, 0.92
            classifier = "bert-nli+daily-calibration"
        elif domain_hits:
            normalized_professional = max(normalized_professional, 0.78)
            normalized_daily = 1 - normalized_professional
            classifier = "bert-nli+domain-calibration"

        label = "professional" if normalized_professional >= normalized_daily else "daily"
        return IntentResult(
            label=label,
            confidence=round(max(normalized_professional, normalized_daily), 4),
            professional_score=round(normalized_professional, 4),
            daily_score=round(normalized_daily, 4),
            classifier=classifier,
        )
    def _fallback(self, question: str, reason: str) -> IntentResult:
        hits = sum(term in question.lower() for term in self._domain_terms)
        professional_score = min(0.92, 0.5 + hits * 0.12)
        daily_score = 1 - professional_score
        label = "professional" if hits else "daily"
        confidence = professional_score if label == "professional" else daily_score
        return IntentResult(
            label=label,
            confidence=round(confidence, 4),
            professional_score=round(professional_score, 4),
            daily_score=round(daily_score, 4),
            classifier="keyword-fallback",
            fallback_reason=reason[:300],
        )
