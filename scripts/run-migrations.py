"""Apply pending PostgreSQL migrations for the local Design_RAG backend."""

from app.db.migrations import apply_migrations

if __name__ == "__main__":
    applied = apply_migrations()
    print({"applied": applied, "count": len(applied)})
