"""PostgreSQL persistence for local, auditable effect-render/material consistency runs."""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connection


TERMINAL_STATUSES = {"succeeded", "partially_succeeded", "failed", "cancelled"}


class MaterialConsistencyRepository:
    """Stores audit snapshots and details; never returns archive paths to API callers."""

    def list_sources(self, project_id: str) -> dict[str, object]:
        project_uuid = UUID(project_id)
        with connection() as conn:
            project = conn.execute("SELECT id, name, external_key FROM projects WHERE id = %s", (project_uuid,)).fetchone()
            if not project:
                raise ValueError("Project not found")
            images = conn.execute(
                """
                SELECT j.id AS source_id, 'ingestion_job'::text AS source_type, j.id AS ingestion_job_id,
                       j.document_id, COALESCE(j.source_file_name, d.file_name) AS file_name,
                       NULL::integer AS page_number, d.file_type, j.status AS ingestion_status,
                       visual.id AS visual_analysis_job_id, visual.status AS visual_analysis_status,
                       visual.analysis AS visual_analysis, TRUE AS can_analyze,
                       NULL::text AS image_classification, NULL::text AS processing_status
                FROM ingestion_jobs j
                JOIN documents d ON d.id = j.document_id
                LEFT JOIN LATERAL (
                    SELECT id, status, analysis FROM visual_analysis_jobs
                    WHERE ingestion_job_id = j.id
                    ORDER BY (status = 'succeeded') DESC, created_at DESC LIMIT 1
                ) visual ON TRUE
                WHERE d.project_id = %s AND d.status = 'active'
                  AND lower(d.file_type) IN ('png', 'jpg', 'jpeg', 'webp')
                ORDER BY COALESCE(j.source_file_name, d.file_name), j.created_at
                """,
                (project_uuid,),
            ).fetchall()
            assets = conn.execute(
                """
                SELECT ia.id AS source_id, 'image_asset'::text AS source_type, da.ingestion_job_id,
                       ia.document_id, COALESCE(da.file_name, d.file_name, '提取图片') AS file_name,
                       ia.page_number, d.file_type, NULL::text AS ingestion_status,
                       NULL::uuid AS visual_analysis_job_id, ia.vision_status AS visual_analysis_status,
                       ia.vision_analysis AS visual_analysis, (da.storage_path IS NOT NULL) AS can_analyze,
                       ia.image_classification, ia.processing_status
                FROM image_assets ia
                JOIN documents d ON d.id = ia.document_id
                LEFT JOIN document_assets da ON da.id = ia.document_asset_id
                JOIN document_versions dv ON dv.id = ia.document_version_id
                WHERE d.project_id = %s AND d.status = 'active'
                  AND dv.version_no = (SELECT MAX(v.version_no) FROM document_versions v WHERE v.document_id = d.id)
                  AND ia.image_classification IN ('rendering', 'furniture_image', 'material_board', 'material_swatch')
                ORDER BY d.file_name, ia.page_number NULLS LAST, ia.created_at
                """,
                (project_uuid,),
            ).fetchall()
            documents = conn.execute(
                """
                SELECT d.id AS document_id, d.file_name, d.file_type,
                       COUNT(c.id)::int AS evidence_count,
                       MIN(c.page_start) AS first_page
                FROM documents d
                JOIN document_versions dv ON dv.document_id = d.id
                JOIN chunks c ON c.document_version_id = dv.id
                WHERE d.project_id = %s AND d.status = 'active'
                  AND dv.version_no = (SELECT MAX(v.version_no) FROM document_versions v WHERE v.document_id = d.id)
                  AND c.chunk_type IN ('material_item', 'material_swatch', 'table', 'document_text')
                  AND COALESCE(c.metadata->>'content_origin', 'document_text') <> 'vision_generated'
                GROUP BY d.id, d.file_name, d.file_type
                ORDER BY evidence_count DESC, d.file_name
                """,
                (project_uuid,),
            ).fetchall()
        image_items = [self._public_source(dict(row)) for row in [*images, *assets]]
        return {
            "project": {"id": str(project["id"]), "name": project["name"], "external_key": project["external_key"]},
            "images": image_items,
            "material_documents": [self._public_material_document(dict(row)) for row in documents],
        }

    def list_material_evidence(self, project_id: str, document_ids: list[str]) -> list[dict[str, object]]:
        if not document_ids:
            return []
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.content, c.chunk_type, c.metadata, c.page_start, c.page_end,
                       c.section_path, d.id AS document_id, d.file_name, d.file_type
                FROM chunks c
                JOIN document_versions dv ON dv.id = c.document_version_id
                JOIN documents d ON d.id = dv.document_id
                WHERE d.project_id = %s AND d.id = ANY(%s)
                  AND d.status = 'active'
                  AND dv.version_no = (SELECT MAX(v.version_no) FROM document_versions v WHERE v.document_id = d.id)
                  AND c.chunk_type IN ('material_item', 'material_swatch', 'table', 'document_text')
                  AND COALESCE(c.metadata->>'content_origin', 'document_text') <> 'vision_generated'
                ORDER BY CASE c.chunk_type WHEN 'material_item' THEN 0 WHEN 'material_swatch' THEN 1 WHEN 'table' THEN 2 ELSE 3 END,
                         d.file_name, c.page_start NULLS LAST, c.id
                """,
                (UUID(project_id), [UUID(value) for value in document_ids]),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_audit(self, *, project_id: str, status: str, selected_sources: list[dict[str, object]],
                     material_document_ids: list[str], approval_record: dict[str, object], provider_name: str | None,
                     model_id: str, prompt_version: str, progress: dict[str, object]) -> dict[str, object]:
        audit_id = uuid4()
        with connection() as conn:
            conn.execute(
                """INSERT INTO material_consistency_audits
                   (id, project_id, status, selected_sources, selected_material_document_ids, approval_record,
                    provider_name, model_id, prompt_version, progress)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (audit_id, UUID(project_id), status, Jsonb(selected_sources), Jsonb(material_document_ids), Jsonb(approval_record),
                 provider_name, model_id, prompt_version, Jsonb(progress)),
            )
            conn.commit()
        result = self.get_audit(str(audit_id))
        assert result
        return result

    def get_audit(self, audit_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, p.name AS project_name, p.external_key AS project_external_key
                FROM material_consistency_audits a JOIN projects p ON p.id = a.project_id
                WHERE a.id = %s
                """, (UUID(audit_id),)
            ).fetchone()
            if not row:
                return None
            items = conn.execute(
                """SELECT * FROM material_consistency_items WHERE audit_id = %s
                   ORDER BY source_file_name, page_number NULLS FIRST, created_at, id""", (UUID(audit_id),)
            ).fetchall()
        value = self._public_audit(dict(row))
        value["items"] = [self._public_item(dict(item)) for item in items]
        return value

    def list_audits(self, *, project_id: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        clauses, params = [], []
        if project_id:
            clauses.append("a.project_id = %s")
            params.append(UUID(project_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, p.name AS project_name, p.external_key AS project_external_key,
                       COUNT(i.id)::int AS item_count
                FROM material_consistency_audits a
                JOIN projects p ON p.id = a.project_id
                LEFT JOIN material_consistency_items i ON i.audit_id = a.id
                {where}
                GROUP BY a.id, p.name, p.external_key
                ORDER BY a.created_at DESC LIMIT %s
                """, (*params, limit)
            ).fetchall()
        return [self._public_audit(dict(row)) for row in rows]

    def get_sources_for_audit(self, audit_id: str) -> list[dict[str, object]]:
        audit = self.get_audit(audit_id)
        if not audit:
            return []
        source_specs = audit.get("selected_sources") or []
        if not isinstance(source_specs, list):
            return []
        result: list[dict[str, object]] = []
        for spec in source_specs:
            if not isinstance(spec, dict):
                continue
            item = self.get_source(project_id=str(audit["project_id"]), source_type=str(spec.get("source_type") or ""), source_id=str(spec.get("source_id") or ""), include_local_path=True)
            if item:
                result.append(item)
        return result

    def get_source(self, *, project_id: str, source_type: str, source_id: str, include_local_path: bool = False) -> dict[str, object] | None:
        if source_type not in {"ingestion_job", "image_asset"}:
            return None
        with connection() as conn:
            if source_type == "ingestion_job":
                row = conn.execute(
                    """
                    SELECT j.id AS source_id, 'ingestion_job'::text AS source_type, j.id AS ingestion_job_id, j.document_id,
                           COALESCE(j.source_file_name, d.file_name) AS file_name, NULL::integer AS page_number, d.file_type,
                           j.status AS ingestion_status, j.source_file_path AS local_path,
                           visual.id AS visual_analysis_job_id, visual.status AS visual_analysis_status, visual.analysis AS visual_analysis,
                           TRUE AS can_analyze, NULL::text AS image_classification, NULL::text AS processing_status
                    FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id
                    LEFT JOIN LATERAL (SELECT id, status, analysis FROM visual_analysis_jobs WHERE ingestion_job_id = j.id
                                       ORDER BY (status = 'succeeded') DESC, created_at DESC LIMIT 1) visual ON TRUE
                    WHERE d.project_id = %s AND j.id = %s
                    """, (UUID(project_id), UUID(source_id))
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT ia.id AS source_id, 'image_asset'::text AS source_type, da.ingestion_job_id, ia.document_id,
                           COALESCE(da.file_name, d.file_name, '提取图片') AS file_name, ia.page_number, d.file_type,
                           NULL::text AS ingestion_status, da.storage_path AS local_path,
                           NULL::uuid AS visual_analysis_job_id, ia.vision_status AS visual_analysis_status, ia.vision_analysis AS visual_analysis,
                           (da.storage_path IS NOT NULL) AS can_analyze, ia.image_classification, ia.processing_status
                    FROM image_assets ia JOIN documents d ON d.id = ia.document_id
                    LEFT JOIN document_assets da ON da.id = ia.document_asset_id
                    WHERE d.project_id = %s AND ia.id = %s
                    """, (UUID(project_id), UUID(source_id))
                ).fetchone()
        if not row:
            return None
        value = dict(row)
        return value if include_local_path else self._public_source(value)

    def save_image_asset_visual_analysis(self, *, source_id: str, analysis: dict[str, object],
                                         provider_name: str | None, model_id: str, prompt_version: str) -> None:
        """Persist structured output for an extracted page; source bytes stay in local storage."""
        with connection() as conn:
            conn.execute(
                """UPDATE image_assets
                   SET vision_analysis = %s, vision_status = 'succeeded', vision_provider_name = %s,
                       vision_model_id = %s, vision_prompt_version = %s, vision_updated_at = NOW(), updated_at = NOW()
                   WHERE id = %s""",
                (Jsonb(analysis), provider_name, model_id, prompt_version, UUID(source_id)),
            )
            conn.commit()

    def promote_waiting_audits(self) -> int:
        """Move waiting jobs once visual jobs settle; failed/cancelled images are audited as missing evidence."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, selected_sources FROM material_consistency_audits
                WHERE status = 'waiting_for_visual' AND cancel_requested_at IS NULL
                FOR UPDATE SKIP LOCKED
                """
            ).fetchall()
            promoted = 0
            for row in rows:
                specs = row["selected_sources"] if isinstance(row["selected_sources"], list) else []
                image_ids = [UUID(str(spec.get("source_id"))) for spec in specs if isinstance(spec, dict) and spec.get("source_type") == "ingestion_job"]
                if not image_ids:
                    conn.execute("UPDATE material_consistency_audits SET status = 'queued', updated_at = NOW() WHERE id = %s", (row["id"],))
                    promoted += 1
                    continue
                states = conn.execute(
                    """SELECT ingestion_job_id, status FROM visual_analysis_jobs
                       WHERE ingestion_job_id = ANY(%s)
                       AND created_at = (SELECT MAX(v2.created_at) FROM visual_analysis_jobs v2 WHERE v2.ingestion_job_id = visual_analysis_jobs.ingestion_job_id)""",
                    (image_ids,),
                ).fetchall()
                by_id = {str(state["ingestion_job_id"]): state["status"] for state in states}
                if all(by_id.get(str(identifier)) in {"succeeded", "failed", "cancelled"} for identifier in image_ids):
                    conn.execute("UPDATE material_consistency_audits SET status = 'queued', progress = progress || %s, updated_at = NOW() WHERE id = %s",
                                 (Jsonb({"stage": "visual_ready", "message": "视觉分析已完成，等待核对"}), row["id"]))
                    promoted += 1
            conn.commit()
        return promoted

    def recover_inflight_audits(self) -> int:
        with connection() as conn:
            rows = conn.execute(
                """UPDATE material_consistency_audits
                   SET status = CASE WHEN cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE 'queued' END,
                       completed_at = CASE WHEN cancel_requested_at IS NOT NULL THEN NOW() ELSE completed_at END,
                       progress = progress || %s, updated_at = NOW()
                   WHERE status = 'running' RETURNING id""",
                (Jsonb({"stage": "recovered", "message": "本地服务重启后已恢复核对队列"}),)
            ).fetchall()
            conn.commit()
        return len(rows)

    def claim_next_audit(self) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                WITH candidate AS (
                    SELECT id FROM material_consistency_audits
                    WHERE status = 'queued' AND cancel_requested_at IS NULL
                    ORDER BY created_at ASC FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE material_consistency_audits a
                SET status = 'running', started_at = COALESCE(started_at, NOW()),
                    progress = progress || %s, error_code = NULL, error_message = NULL, updated_at = NOW()
                FROM candidate WHERE a.id = candidate.id RETURNING a.*
                """, (Jsonb({"stage": "running", "message": "正在核对效果图与物料资料"}),)
            ).fetchone()
            conn.commit()
        return dict(row) if row else None

    def update_progress(self, audit_id: str, progress: dict[str, object]) -> None:
        with connection() as conn:
            conn.execute("UPDATE material_consistency_audits SET progress = %s, updated_at = NOW() WHERE id = %s", (Jsonb(progress), UUID(audit_id)))
            conn.commit()

    def cancellation_requested(self, audit_id: str) -> bool:
        with connection() as conn:
            row = conn.execute("SELECT cancel_requested_at FROM material_consistency_audits WHERE id = %s", (UUID(audit_id),)).fetchone()
        return bool(row and row["cancel_requested_at"] is not None)

    def request_cancel(self, audit_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute("SELECT status FROM material_consistency_audits WHERE id = %s FOR UPDATE", (UUID(audit_id),)).fetchone()
            if not row:
                return None
            if row["status"] in TERMINAL_STATUSES:
                conn.commit()
                return self.get_audit(audit_id)
            immediate = row["status"] in {"queued", "waiting_for_visual"}
            conn.execute(
                """UPDATE material_consistency_audits
                   SET cancel_requested_at = NOW(), cancel_reason = 'CANCELLED_BY_USER',
                       status = CASE WHEN %s THEN 'cancelled' ELSE status END,
                       completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END,
                       progress = progress || %s, updated_at = NOW() WHERE id = %s""",
                (immediate, immediate, Jsonb({"stage": "cancel_requested", "message": "已请求取消核对任务"}), UUID(audit_id)),
            )
            conn.commit()
        return self.get_audit(audit_id)

    def replace_items(self, audit_id: str, items: list[dict[str, object]]) -> None:
        with connection() as conn:
            conn.execute("DELETE FROM material_consistency_items WHERE audit_id = %s", (UUID(audit_id),))
            for item in items:
                conn.execute(
                    """INSERT INTO material_consistency_items
                       (id, audit_id, source_type, source_id, ingestion_job_id, document_id, source_file_name, page_number,
                        component_kind, component_name, component_region, visual_material, material_requirement, verdict,
                        confidence, reason, evidence, ai_judgement)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (uuid4(), UUID(audit_id), item["source_type"], UUID(item["source_id"]),
                     UUID(item["ingestion_job_id"]) if item.get("ingestion_job_id") else None,
                     UUID(item["document_id"]) if item.get("document_id") else None, item["source_file_name"], item.get("page_number"),
                     item["component_kind"], item["component_name"], Jsonb(item.get("component_region") or {}),
                     Jsonb(item.get("visual_material") or {}), Jsonb(item.get("material_requirement") or {}), item["verdict"],
                     float(item["confidence"]), item["reason"], Jsonb(item.get("evidence") or []), Jsonb(item.get("ai_judgement") or {})),
                )
            conn.commit()

    def finish_audit(self, *, audit_id: str, status: str, summary: dict[str, object], error_code: str | None = None,
                     error_message: str | None = None) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE material_consistency_audits
                   SET status = %s, summary = %s, error_code = %s, error_message = %s,
                       progress = %s, completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                (status, Jsonb(summary), error_code, error_message,
                 Jsonb({"stage": status, "message": "核对已完成" if status in {"succeeded", "partially_succeeded"} else "核对任务结束"}), UUID(audit_id)),
            )
            conn.commit()

    @staticmethod
    def _public_source(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "source_id", "source_type", "ingestion_job_id", "document_id", "file_name", "page_number", "file_type",
            "ingestion_status", "visual_analysis_job_id", "visual_analysis_status", "can_analyze", "image_classification", "processing_status",
        )}

    @staticmethod
    def _public_material_document(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in ("document_id", "file_name", "file_type", "evidence_count", "first_page")}

    @staticmethod
    def _public_audit(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "project_id", "project_name", "project_external_key", "status", "selected_sources",
            "selected_material_document_ids", "approval_record", "provider_name", "model_id", "prompt_version",
            "summary", "progress", "error_code", "error_message", "cancel_requested_at", "cancel_reason",
            "item_count", "created_at", "started_at", "completed_at", "updated_at",
        )}

    @staticmethod
    def _public_item(row: dict[str, object]) -> dict[str, object]:
        return {key: row.get(key) for key in (
            "id", "audit_id", "source_type", "source_id", "ingestion_job_id", "document_id", "source_file_name", "page_number",
            "component_kind", "component_name", "component_region", "visual_material", "material_requirement", "verdict",
            "confidence", "reason", "evidence", "ai_judgement", "created_at", "updated_at",
        )}
