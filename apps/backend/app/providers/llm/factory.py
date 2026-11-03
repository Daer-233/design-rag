"""LLM provider factory."""

from functools import lru_cache

from app.providers.llm.base import AnswerProvider, VisionProvider
from app.providers.llm.openai_compatible import OpenAICompatibleVisionProvider
from app.providers.llm.openai_compatible_answer import OpenAICompatibleAnswerProvider


@lru_cache
def get_vision_provider() -> VisionProvider:
    return OpenAICompatibleVisionProvider()


@lru_cache
def get_answer_provider() -> AnswerProvider:
    return OpenAICompatibleAnswerProvider()
