"""Authoritative PostgreSQL persistence for the anonymous local Agent."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from app.db.connection import connection


class AgentRepository:
    """Persist isolated anonymous sessions, conversation memory and SSE event history."""

    @staticmethod
    def _uuid(value: str) -> UUID:
        return UUID(str(value))

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for key in ("citations", "metadata", "payload"):
            if isinstance(value.get(key), str):
                try:
                    value[key] = json.loads(value[key])
                except json.JSONDecodeError:
                    pass
        return value

    def touch_anonymous_client(self, anonymous_client_id: str) -> str:
        client_id = self._uuid(anonymous_client_id)
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_anonymous_clients (id)
                VALUES (%s)
                ON CONFLICT (id) DO UPDATE SET last_seen_at = NOW()
                """,
                (client_id,),
            )
            conn.commit()
        return str(client_id)

    def create_conversation(self, anonymous_client_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        conversation_id = uuid4()
        client_id = self._uuid(anonymous_client_id)
        project_uuid = self._uuid(project_id) if project_id else None
        with connection() as conn:
            row = conn.execute(
                """
                INSERT INTO agent_conversations (id, anonymous_client_id, project_id)
                VALUES (%s, %s, %s)
                RETURNING id, title, project_id, summary, created_at, updated_at
                """,
                (conversation_id, client_id, project_uuid),
            ).fetchone()
            conn.commit()
        return self._normalize_row(row) or {}

    def list_conversations(self, anonymous_client_id: str) -> list[dict[str, Any]]:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.title, c.project_id, c.summary, c.created_at, c.updated_at,
                       (SELECT m.content FROM agent_messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.created_at DESC LIMIT 1) AS last_message
                FROM agent_conversations c
                WHERE c.anonymous_client_id = %s
                ORDER BY c.updated_at DESC
                """,
                (self._uuid(anonymous_client_id),),
            ).fetchall()
        return [self._normalize_row(row) or {} for row in rows]

    def get_conversation(self, anonymous_client_id: str, conversation_id: str) -> dict[str, Any] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT id, title, project_id, summary, created_at, updated_at
                FROM agent_conversations
                WHERE id = %s AND anonymous_client_id = %s
                """,
                (self._uuid(conversation_id), self._uuid(anonymous_client_id)),
            ).fetchone()
        return self._normalize_row(row)

    def list_messages(self, anonymous_client_id: str, conversation_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if self.get_conversation(anonymous_client_id, conversation_id) is None:
            return []
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, run_id, role, content, content_origin, citations, metadata, created_at
                FROM agent_messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (self._uuid(conversation_id), max(1, min(limit, 500))),
            ).fetchall()
        return [self._normalize_row(row) or {} for row in rows]

    def list_retrievable_projects(self) -> list[dict[str, Any]]:
        """Return project identities that currently have active source documents."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.name, p.external_key
                FROM projects p
                WHERE EXISTS (
                    SELECT 1
                    FROM documents d
                    WHERE d.project_id = p.id AND d.status = 'active'
                )
                ORDER BY p.name ASC
                """
            ).fetchall()
        return [self._normalize_row(row) or {} for row in rows]

    def set_conversation_project(self, conversation_id: str, project_id: str | None) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET project_id = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (
                    self._uuid(project_id) if project_id else None,
                    self._uuid(conversation_id),
                ),
            )
            conn.commit()
    def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        content_origin: str,
        run_id: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_id = uuid4()
        with connection() as conn:
            row = conn.execute(
                """
                INSERT INTO agent_messages
                    (id, conversation_id, run_id, role, content, content_origin, citations, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                RETURNING id, conversation_id, run_id, role, content, content_origin, citations, metadata, created_at
                """,
                (
                    message_id,
                    self._uuid(conversation_id),
                    self._uuid(run_id) if run_id else None,
                    role,
                    content,
                    content_origin,
                    self._json(citations or []),
                    self._json(metadata or {}),
                ),
            ).fetchone()
            conn.execute(
                "UPDATE agent_conversations SET updated_at = NOW() WHERE id = %s",
                (self._uuid(conversation_id),),
            )
            conn.commit()
        return self._normalize_row(row) or {}

    def create_run(
        self,
        *,
        conversation_id: str,
        query_text: str,
        project_id: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        run_id = uuid4()
        with connection() as conn:
            row = conn.execute(
                """
                INSERT INTO agent_runs (id, conversation_id, status, request_id, query_text, project_id)
                VALUES (%s, %s, 'queued', %s, %s, %s)
                RETURNING id, conversation_id, status, request_id, query_text, project_id, created_at
                """,
                (
                    run_id,
                    self._uuid(conversation_id),
                    request_id,
                    query_text,
                    self._uuid(project_id) if project_id else None,
                ),
            ).fetchone()
            conn.commit()
        return self._normalize_row(row) or {}

    def find_run_by_request_id(
        self, anonymous_client_id: str, conversation_id: str, request_id: str
    ) -> dict[str, Any] | None:
        if self.get_conversation(anonymous_client_id, conversation_id) is None:
            return None
        with connection() as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, status, request_id, query_text, project_id,
                       intent_label, intent_confidence, rewritten_query, reasoning_summary,
                       error_code, error_message, started_at, completed_at, created_at
                FROM agent_runs
                WHERE conversation_id = %s AND request_id = %s
                """,
                (self._uuid(conversation_id), request_id),
            ).fetchone()
        return self._normalize_row(row)

    def get_run(self, anonymous_client_id: str, run_id: str) -> dict[str, Any] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT r.id, r.conversation_id, r.status, r.request_id, r.query_text, r.project_id,
                       r.intent_label, r.intent_confidence, r.rewritten_query, r.reasoning_summary,
                       r.error_code, r.error_message, r.started_at, r.completed_at, r.created_at
                FROM agent_runs r
                JOIN agent_conversations c ON c.id = r.conversation_id
                WHERE r.id = %s AND c.anonymous_client_id = %s
                """,
                (self._uuid(run_id), self._uuid(anonymous_client_id)),
            ).fetchone()
        return self._normalize_row(row)

    def mark_run_running(self, run_id: str) -> None:
        with connection() as conn:
            conn.execute(
                "UPDATE agent_runs SET status = 'running', started_at = NOW() WHERE id = %s AND status = 'queued'",
                (self._uuid(run_id),),
            )
            conn.commit()

    def complete_run(
        self,
        run_id: str,
        *,
        intent_label: str,
        intent_confidence: float,
        rewritten_query: str,
        reasoning_summary: str,
    ) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = 'completed', intent_label = %s, intent_confidence = %s,
                    rewritten_query = %s, reasoning_summary = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (intent_label, intent_confidence, rewritten_query, reasoning_summary, self._uuid(run_id)),
            )
            conn.commit()

    def fail_run(self, run_id: str, *, error_code: str, error_message: str) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = 'failed', error_code = %s, error_message = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (error_code[:120], error_message[:1000], self._uuid(run_id)),
            )
            conn.commit()

    def cancel_run(self, anonymous_client_id: str, run_id: str) -> bool:
        run = self.get_run(anonymous_client_id, run_id)
        if run is None or run["status"] in {"completed", "failed", "cancelled"}:
            return False
        with connection() as conn:
            conn.execute(
                "UPDATE agent_runs SET status = 'cancelled', completed_at = NOW() WHERE id = %s",
                (self._uuid(run_id),),
            )
            conn.commit()
        return True

    def run_allows_asset(self, anonymous_client_id: str, run_id: str, asset_id: str) -> bool:
        """Check that an asset was returned by this anonymous Chat run."""
        with connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM agent_runs r
                JOIN agent_conversations c ON c.id = r.conversation_id
                WHERE r.id = %s AND c.anonymous_client_id = %s
                  AND (
                    EXISTS (
                      SELECT 1 FROM agent_messages m
                      WHERE m.run_id = r.id
                        AND m.metadata @> jsonb_build_object(
                          'images', jsonb_build_array(jsonb_build_object('asset_id', %s::text))
                        )
                    )
                    OR EXISTS (
                      SELECT 1 FROM agent_workflow_events e
                      WHERE e.run_id = r.id
                        AND e.payload @> jsonb_build_object(
                          'images', jsonb_build_array(jsonb_build_object('asset_id', %s::text))
                        )
                    )
                  )
                LIMIT 1
                """,
                (self._uuid(run_id), self._uuid(anonymous_client_id), str(asset_id), str(asset_id)),
            ).fetchone()
        return row is not None
    def add_event(
        self,
        *,
        run_id: str,
        event_type: str,
        node_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with connection() as conn:
            row = conn.execute(
                """
                INSERT INTO agent_workflow_events (run_id, event_type, node_name, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id, run_id, event_type, node_name, payload, created_at
                """,
                (self._uuid(run_id), event_type, node_name, self._json(payload or {})),
            ).fetchone()
            conn.commit()
        return self._normalize_row(row) or {}

    def list_events(self, anonymous_client_id: str, run_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        if self.get_run(anonymous_client_id, run_id) is None:
            return []
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, event_type, node_name, payload, created_at
                FROM agent_workflow_events
                WHERE run_id = %s AND id > %s
                ORDER BY id ASC
                """,
                (self._uuid(run_id), max(0, after_id)),
            ).fetchall()
        return [self._normalize_row(row) or {} for row in rows]

    def rename_conversation(self, anonymous_client_id: str, conversation_id: str, title: str) -> dict[str, Any] | None:
        if self.get_conversation(anonymous_client_id, conversation_id) is None:
            return None
        with connection() as conn:
            row = conn.execute(
                """
                UPDATE agent_conversations SET title = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, title, project_id, summary, created_at, updated_at
                """,
                (title.strip()[:100] or '新对话', self._uuid(conversation_id)),
            ).fetchone()
            conn.commit()
        return self._normalize_row(row)

    def delete_conversation(self, anonymous_client_id: str, conversation_id: str) -> bool:
        with connection() as conn:
            row = conn.execute(
                """
                DELETE FROM agent_conversations
                WHERE id = %s AND anonymous_client_id = %s
                RETURNING id
                """,
                (self._uuid(conversation_id), self._uuid(anonymous_client_id)),
            ).fetchone()
            conn.commit()
        return row is not None

    def set_conversation_title_if_default(self, conversation_id: str, query_text: str) -> None:
        title = query_text.strip().replace("\n", " ")[:48] or "新对话"
        with connection() as conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET title = %s, updated_at = NOW()
                WHERE id = %s AND title = '新对话'
                """,
                (title, self._uuid(conversation_id)),
            )
            conn.commit()

