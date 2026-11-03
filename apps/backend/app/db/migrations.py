"""Small SQL migration runner used before Alembic is needed."""

from pathlib import Path

from app.db.connection import connection

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_migrations() -> list[str]:
    applied: list[str] = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        existing = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in files:
            if migration.name in existing:
                continue
            conn.execute(migration.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (migration.name,))
            applied.append(migration.name)
        conn.commit()
    return applied
