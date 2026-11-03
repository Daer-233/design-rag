"""PostgreSQL repository for authoritative retrieval-audit records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connection


@dataclass(frozen=True)
class SearchAuditWrite:
    actor_id: str
    actor_display_name: str | None
    identity_source: str
    principal_roles: list[str]
    project_id: str | None
    query_text: str
    requested_evidence_top_k: int | None
    result_chunk_ids: list[str]
    outcome: str
    error_code: str | None
    latency_ms: float | None
    client_ip: str | None
    client_user_agent: str | None
    agent_run_id: str | None = None
    conversation_id: str | None = None
    retrieval_query_text: str | None = None


class SearchAuditRepository:
    """Persist query feedback in PostgreSQL; Qdrant remains retrieval-index only."""

    def record(self, item: SearchAuditWrite) -> str:
        audit_id = uuid4()
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO search_audit_logs (
                    id, request_id, actor_id, actor_display_name, identity_source,
                    principal_roles, project_id, query_text, query_sha256,
                    requested_evidence_top_k, result_count, result_chunk_ids, outcome,
                    error_code, latency_ms, client_ip, client_user_agent,
                    agent_run_id, conversation_id, retrieval_query_text
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    audit_id,
                    uuid4(),
                    item.actor_id,
                    item.actor_display_name,
                    item.identity_source,
                    Jsonb(item.principal_roles),
                    UUID(item.project_id) if item.project_id else None,
                    item.query_text,
                    sha256(item.query_text.encode("utf-8")).hexdigest(),
                    item.requested_evidence_top_k,
                    len(item.result_chunk_ids),
                    item.result_chunk_ids,
                    item.outcome,
                    item.error_code,
                    item.latency_ms,
                    item.client_ip,
                    item.client_user_agent,
                    UUID(item.agent_run_id) if item.agent_run_id else None,
                    UUID(item.conversation_id) if item.conversation_id else None,
                    item.retrieval_query_text,
                ),
            )
            conn.commit()
        return str(audit_id)

    def list_records(
        self,
        *,
        limit: int,
        offset: int,
        project_id: str | None = None,
        actor_id: str | None = None,
        outcome: str | None = None,
        query: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, object]:
        clauses: list[str] = []
        params: list[object] = []
        if project_id:
            clauses.append("a.project_id = %s")
            params.append(UUID(project_id))
        if actor_id:
            clauses.append("a.actor_id ILIKE %s")
            params.append(f"%{actor_id}%")
        if outcome:
            clauses.append("a.outcome = %s")
            params.append(outcome)
        if query:
            clauses.append("a.query_text ILIKE %s")
            params.append(f"%{query}%")
        if since:
            clauses.append("a.requested_at >= %s")
            params.append(since)
        if until:
            clauses.append("a.requested_at <= %s")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*)::int AS total FROM search_audit_logs a {where}", params
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT a.id, a.requested_at, a.actor_id, a.actor_display_name,
                       a.identity_source, a.principal_roles, a.project_id,
                       COALESCE(p.name, 'Unknown project') AS project_name,
                       COALESCE(p.external_key, '') AS project_external_key,
                       a.query_text, a.query_sha256, a.requested_evidence_top_k,
                       a.result_count, a.result_chunk_ids, a.outcome, a.error_code,
                       a.latency_ms, a.client_ip, a.client_user_agent,
                       a.agent_run_id, a.conversation_id, a.retrieval_query_text
                FROM search_audit_logs a
                LEFT JOIN projects p ON p.id = a.project_id
                {where}
                ORDER BY a.requested_at DESC, a.id DESC
                LIMIT %s OFFSET %s
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_public(row) for row in rows],
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }

    def summary(self, *, days: int) -> dict[str, object]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with connection() as conn:
            totals = conn.execute(
                """
                SELECT COUNT(*)::int AS total_searches,
                       COUNT(*) FILTER (WHERE outcome = 'succeeded')::int AS succeeded_searches,
                       COUNT(*) FILTER (WHERE outcome = 'denied')::int AS denied_searches,
                       COUNT(*) FILTER (WHERE outcome = 'failed')::int AS failed_searches,
                       COUNT(*) FILTER (WHERE outcome = 'succeeded' AND result_count = 0)::int
                           AS zero_result_searches,
                       COALESCE(ROUND(AVG(latency_ms)::numeric, 2), 0)::float8 AS average_latency_ms
                FROM search_audit_logs
                WHERE requested_at >= %s
                """,
                (since,),
            ).fetchone()
            top_queries = conn.execute(
                """
                SELECT query_text, COUNT(*)::int AS search_count,
                       COUNT(*) FILTER (WHERE outcome = 'succeeded' AND result_count = 0)::int
                           AS zero_result_count,
                       MAX(requested_at) AS last_requested_at
                FROM search_audit_logs
                WHERE requested_at >= %s
                GROUP BY query_text
                ORDER BY search_count DESC, last_requested_at DESC
                LIMIT 12
                """,
                (since,),
            ).fetchall()
        return {
            "days": days,
            "since": since.isoformat(),
            **dict(totals),
            "top_queries": [
                {
                    "query_text": row["query_text"],
                    "search_count": int(row["search_count"]),
                    "zero_result_count": int(row["zero_result_count"]),
                    "last_requested_at": row["last_requested_at"].isoformat(),
                }
                for row in top_queries
            ],
        }

    def list_projects(self) -> list[dict[str, str]]:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.external_key, p.name
                FROM projects p
                WHERE COALESCE((p.metadata ->> 'admin_hidden')::boolean, FALSE) = FALSE
                  AND EXISTS (
                    SELECT 1
                    FROM documents d
                    WHERE d.project_id = p.id
                )
                ORDER BY p.name, p.external_key
                """
            ).fetchall()
        return [
            {"id": str(row["id"]), "external_key": row["external_key"], "name": row["name"]}
            for row in rows
        ]

    @staticmethod
    def _row_to_public(row: dict[str, object]) -> dict[str, object]:
        requested_at = row["requested_at"]
        return {
            "id": str(row["id"]),
            "requested_at": requested_at.isoformat() if isinstance(requested_at, datetime) else requested_at,
            "actor_id": row["actor_id"],
            "actor_display_name": row["actor_display_name"],
            "identity_source": row["identity_source"],
            "principal_roles": row["principal_roles"],
            "project": {
                "id": str(row["project_id"]) if row["project_id"] else None,
                "name": row["project_name"],
                "external_key": row["project_external_key"],
            },
            "query_text": row["query_text"],
            "query_sha256": row["query_sha256"],
            "requested_evidence_top_k": row["requested_evidence_top_k"],
            "result_count": row["result_count"],
            "result_chunk_ids": row["result_chunk_ids"],
            "outcome": row["outcome"],
            "error_code": row["error_code"],
            "latency_ms": row["latency_ms"],
            "client_ip": row["client_ip"],
            "client_user_agent": row["client_user_agent"],
            "agent_run_id": str(row["agent_run_id"]) if row.get("agent_run_id") else None,
            "conversation_id": str(row["conversation_id"]) if row.get("conversation_id") else None,
            "retrieval_query_text": row.get("retrieval_query_text"),
        }
