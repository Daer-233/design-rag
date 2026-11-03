"""PostgreSQL connection helpers. PostgreSQL remains the system of record."""

from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from app.core.config import settings


def normalized_postgres_dsn(dsn: str) -> str:
    """Make SQLAlchemy-style DSNs usable by psycopg without exposing credentials."""
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(normalized_postgres_dsn(settings.postgres_dsn), row_factory=dict_row) as conn:
        yield conn


def check_postgres() -> bool:
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False
