"""Parser provider factory."""

from functools import lru_cache

from app.providers.parser.base import ParserProvider
from app.providers.parser.mineru_provider import MinerUParserProvider


@lru_cache
def get_parser_provider() -> ParserProvider:
    return MinerUParserProvider()
