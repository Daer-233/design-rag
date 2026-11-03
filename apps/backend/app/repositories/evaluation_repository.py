"""PostgreSQL repository for auditable, local RAGAS evaluation data."""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connection


class EvaluationRepository:
    """Persists evaluation suites/runs; external scoring is performed only by the service."""

    def list_suites(self, *, project_id: str | None = None) -> list[dict[str, object]]:
        clauses, params = [], []
        if project_id:
            clauses.append("s.project_id = %s")
            params.append(UUID(project_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT s.id, s.project_id, s.name, s.description, s.created_at, s.updated_at,
                       p.name AS project_name, p.external_key AS project_external_key,
                       COUNT(c.id)::int AS case_count,
                       COUNT(c.id) FILTER (WHERE c.enabled)::int AS enabled_case_count
                FROM rag_evaluation_suites s
                JOIN projects p ON p.id = s.project_id
                LEFT JOIN rag_evaluation_cases c ON c.suite_id = s.id
                {where}
                GROUP BY s.id, p.name, p.external_key
                ORDER BY s.updated_at DESC, s.created_at DESC
                """,
                params,
            ).fetchall()
        return [self._public_suite(row) for row in rows]

    def get_suite(self, suite_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT s.id, s.project_id, s.name, s.description, s.created_at, s.updated_at,
                       p.name AS project_name, p.external_key AS project_external_key
                FROM rag_evaluation_suites s
                JOIN projects p ON p.id = s.project_id
                WHERE s.id = %s
                """,
                (UUID(suite_id),),
            ).fetchone()
            if not row:
                return None
            cases = conn.execute(
                """
                SELECT id, suite_id, question, reference_answer, enabled, sort_order,
                       created_at, updated_at
                FROM rag_evaluation_cases
                WHERE suite_id = %s
                ORDER BY enabled DESC, sort_order ASC, created_at ASC
                """,
                (UUID(suite_id),),
            ).fetchall()
        suite = self._public_suite(row)
        suite["cases"] = [self._public_case(case) for case in cases]
        return suite

    def create_suite(self, *, project_id: str, name: str, description: str) -> dict[str, object]:
        suite_id = uuid4()
        with connection() as conn:
            project = conn.execute("SELECT id FROM projects WHERE id = %s", (UUID(project_id),)).fetchone()
            if not project:
                raise ValueError("Project not found")
            conn.execute(
                """
                INSERT INTO rag_evaluation_suites (id, project_id, name, description)
                VALUES (%s, %s, %s, %s)
                """,
                (suite_id, UUID(project_id), name, description),
            )
            conn.commit()
        created = self.get_suite(str(suite_id))
        assert created
        return created

    def update_suite(self, *, suite_id: str, name: str, description: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                UPDATE rag_evaluation_suites
                SET name = %s, description = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id
                """,
                (name, description, UUID(suite_id)),
            ).fetchone()
            conn.commit()
        return self.get_suite(suite_id) if row else None

    def create_case(self, *, suite_id: str, question: str, reference_answer: str, enabled: bool, sort_order: int) -> dict[str, object]:
        case_id = uuid4()
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO rag_evaluation_cases (id, suite_id, question, reference_answer, enabled, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (case_id, UUID(suite_id), question, reference_answer, enabled, sort_order),
            )
            conn.execute("UPDATE rag_evaluation_suites SET updated_at = NOW() WHERE id = %s", (UUID(suite_id),))
            conn.commit()
            row = conn.execute("SELECT * FROM rag_evaluation_cases WHERE id = %s", (case_id,)).fetchone()
        assert row
        return self._public_case(row)

    def update_case(self, *, case_id: str, question: str, reference_answer: str, enabled: bool, sort_order: int) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                UPDATE rag_evaluation_cases
                SET question = %s, reference_answer = %s, enabled = %s, sort_order = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (question, reference_answer, enabled, sort_order, UUID(case_id)),
            ).fetchone()
            if row:
                conn.execute("UPDATE rag_evaluation_suites SET updated_at = NOW() WHERE id = %s", (row["suite_id"],))
            conn.commit()
        return self._public_case(row) if row else None

    def delete_case(self, case_id: str) -> bool:
        with connection() as conn:
            row = conn.execute("DELETE FROM rag_evaluation_cases WHERE id = %s RETURNING suite_id", (UUID(case_id),)).fetchone()
            if row:
                conn.execute("UPDATE rag_evaluation_suites SET updated_at = NOW() WHERE id = %s", (row["suite_id"],))
            conn.commit()
        return bool(row)

    def create_run(self, *, suite_id: str, project_id: str, model_id: str, evidence_top_k: int, approval_record: dict[str, object], evaluator_version: str | None) -> dict[str, object]:
        run_id = uuid4()
        with connection() as conn:
            active = conn.execute(
                "SELECT id FROM rag_evaluation_runs WHERE suite_id = %s AND status IN ('queued', 'running') LIMIT 1",
                (UUID(suite_id),),
            ).fetchone()
            if active:
                raise ValueError("This suite already has a queued or running evaluation")
            conn.execute(
                """
                INSERT INTO rag_evaluation_runs
                (id, suite_id, project_id, status, model_id, evidence_top_k, approval_record, evaluator_version)
                VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s)
                """,
                (run_id, UUID(suite_id), UUID(project_id), model_id, evidence_top_k, Jsonb(approval_record), evaluator_version),
            )
            conn.commit()
        run = self.get_run(str(run_id))
        assert run
        return run

    def list_runs(self, *, suite_id: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        clauses, params = [], []
        if suite_id:
            clauses.append("r.suite_id = %s")
            params.append(UUID(suite_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*, s.name AS suite_name, p.name AS project_name,
                       COUNT(cr.id)::int AS case_result_count,
                       COUNT(cr.id) FILTER (WHERE cr.status = 'succeeded')::int AS succeeded_case_count
                FROM rag_evaluation_runs r
                JOIN rag_evaluation_suites s ON s.id = r.suite_id
                JOIN projects p ON p.id = r.project_id
                LEFT JOIN rag_evaluation_case_results cr ON cr.run_id = r.id
                {where}
                GROUP BY r.id, s.name, p.name
                ORDER BY r.created_at DESC
                LIMIT %s
                """,
                [*params, limit],
            ).fetchall()
        return [self._public_run(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT r.*, s.name AS suite_name, p.name AS project_name
                FROM rag_evaluation_runs r
                JOIN rag_evaluation_suites s ON s.id = r.suite_id
                JOIN projects p ON p.id = r.project_id
                WHERE r.id = %s
                """,
                (UUID(run_id),),
            ).fetchone()
            if not row:
                return None
            results = conn.execute(
                """
                SELECT cr.*, c.question, c.reference_answer, c.sort_order
                FROM rag_evaluation_case_results cr
                JOIN rag_evaluation_cases c ON c.id = cr.case_id
                WHERE cr.run_id = %s
                ORDER BY c.sort_order ASC, cr.created_at ASC
                """,
                (UUID(run_id),),
            ).fetchall()
        run = self._public_run(row)
        run["case_results"] = [self._public_case_result(item) for item in results]
        return run

    def claim_next_run(self) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                WITH candidate AS (
                    SELECT id FROM rag_evaluation_runs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE rag_evaluation_runs r
                SET status = 'running', started_at = NOW(), updated_at = NOW(), error_code = NULL, error_message = NULL
                FROM candidate
                WHERE r.id = candidate.id
                RETURNING r.*
                """
            ).fetchone()
            conn.commit()
        return dict(row) if row else None

    def list_enabled_cases(self, suite_id: str) -> list[dict[str, object]]:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM rag_evaluation_cases
                WHERE suite_id = %s AND enabled = TRUE
                ORDER BY sort_order ASC, created_at ASC
                """,
                (UUID(suite_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def store_case_result(self, *, run_id: str, case_id: str, status: str, generated_answer: str | None,
                          retrieved_contexts: list[dict[str, object]], citations: list[dict[str, object]],
                          scores: dict[str, object], error_code: str | None, error_message: str | None,
                          duration_ms: float | None) -> None:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO rag_evaluation_case_results
                (id, run_id, case_id, status, generated_answer, retrieved_contexts, citations, scores,
                 error_code, error_message, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, case_id) DO UPDATE SET
                    status = EXCLUDED.status, generated_answer = EXCLUDED.generated_answer,
                    retrieved_contexts = EXCLUDED.retrieved_contexts, citations = EXCLUDED.citations,
                    scores = EXCLUDED.scores, error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message, duration_ms = EXCLUDED.duration_ms,
                    updated_at = NOW()
                """,
                (uuid4(), UUID(run_id), UUID(case_id), status, generated_answer,
                 Jsonb(retrieved_contexts), Jsonb(citations), Jsonb(scores), error_code, error_message, duration_ms),
            )
            conn.commit()

    def finish_run(self, *, run_id: str, status: str, summary: dict[str, object], error_code: str | None = None,
                   error_message: str | None = None) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE rag_evaluation_runs
                SET status = %s, summary = %s, error_code = %s, error_message = %s,
                    completed_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (status, Jsonb(summary), error_code, error_message, UUID(run_id)),
            )
            conn.commit()

    @staticmethod
    def _public_suite(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "project_id", "project_name", "project_external_key", "name", "description",
            "case_count", "enabled_case_count", "created_at", "updated_at",
        ) if key in row}

    @staticmethod
    def _public_case(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "suite_id", "question", "reference_answer", "enabled", "sort_order", "created_at", "updated_at",
        ) if key in row}

    @staticmethod
    def _public_run(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "suite_id", "suite_name", "project_id", "project_name", "status", "evaluator_name",
            "evaluator_version", "model_id", "evidence_top_k", "approval_record", "summary", "error_code",
            "error_message", "case_result_count", "succeeded_case_count", "created_at", "started_at", "completed_at", "updated_at",
        ) if key in row}

    @staticmethod
    def _public_case_result(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "run_id", "case_id", "question", "reference_answer", "sort_order", "status",
            "generated_answer", "retrieved_contexts", "citations", "scores", "error_code", "error_message",
            "duration_ms", "created_at", "updated_at",
        ) if key in row}
