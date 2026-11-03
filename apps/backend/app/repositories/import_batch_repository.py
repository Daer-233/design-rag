"""PostgreSQL persistence for staged, explicitly approved batch imports."""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connection


class ImportBatchRepository:
    """Stores manifests; archive paths stay server-local."""

    def create_batch(self, *, project_external_key: str, project_name: str, force_reprocess: bool = False) -> dict[str, object]:
        batch_id = uuid4()
        with connection() as conn:
            row = conn.execute(
                """INSERT INTO import_batches
                   (id, project_id, project_external_key, project_name, force_reprocess, status)
                   VALUES (%s, (SELECT id FROM projects WHERE external_key = %s LIMIT 1), %s, %s, %s, 'uploading')
                   RETURNING id""",
                (batch_id, project_external_key, project_external_key, project_name, force_reprocess),
            ).fetchone()
            conn.commit()
        return self.get_batch(str(row["id"])) or {}

    def get_batch(self, batch_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """SELECT id, project_id, project_external_key, project_name, force_reprocess, status, manifest,
                          manifest_sha256, schedule_mode, scheduled_for, schedule_requested_at,
                          mineru_approved, vision_approved, error_code, error_message, created_at, prepared_at, approved_at, completed_at, updated_at
                   FROM import_batches WHERE id = %s""",
                (UUID(batch_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_batches(self, *, limit: int, offset: int, project_id: str | None = None) -> dict[str, object]:
        where, params = ("WHERE project_id = %s", [UUID(project_id)]) if project_id else ("", [])
        with connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS total FROM import_batches {where}", tuple(params)).fetchone()
            rows = conn.execute(
                f"""SELECT id, project_id, project_external_key, project_name, force_reprocess, status, manifest,
                           manifest_sha256, schedule_mode, scheduled_for, schedule_requested_at,
                           mineru_approved, vision_approved, error_code, error_message, created_at, prepared_at, approved_at, completed_at, updated_at
                    FROM import_batches {where} ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s""",
                (*params, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": int(total["total"] if total else 0)}

    def get_item_by_path(self, *, batch_id: str, source_relative_path: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """SELECT id, batch_id, ingestion_job_id, existing_ingestion_job_id, source_relative_path,
                          source_file_name, source_file_type, source_sha256, source_size_bytes,
                          outcome, error_code, error_message, processing_plan, created_at, updated_at
                   FROM import_batch_items WHERE batch_id = %s AND source_relative_path = %s""",
                (UUID(batch_id), source_relative_path),
            ).fetchone()
        return dict(row) if row else None

    def list_items(self, batch_id: str) -> list[dict[str, object]]:
        with connection() as conn:
            rows = conn.execute(
                """SELECT id, batch_id, ingestion_job_id, existing_ingestion_job_id, source_relative_path,
                          source_file_name, source_file_type, source_sha256, source_size_bytes,
                          outcome, error_code, error_message, processing_plan, created_at, updated_at
                   FROM import_batch_items WHERE batch_id = %s ORDER BY created_at ASC, id ASC""",
                (UUID(batch_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_existing_ingestion_job(self, *, project_external_key: str, source_sha256: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """SELECT j.id, j.status, j.processing_route FROM ingestion_jobs j
                   JOIN documents d ON d.id = j.document_id JOIN projects p ON p.id = d.project_id
                   WHERE p.external_key = %s AND j.source_sha256 = %s
                   ORDER BY j.created_at ASC, j.id ASC LIMIT 1""",
                (project_external_key, source_sha256),
            ).fetchone()
        return dict(row) if row else None

    def add_item(self, *, batch_id: str, ingestion_job_id: str | None, existing_ingestion_job_id: str | None,
                 source_relative_path: str, source_file_name: str, source_file_type: str, source_sha256: str,
                 source_size_bytes: int, source_file_path: str, outcome: str,
                 processing_plan: dict[str, object] | None = None, error_code: str | None = None,
                 error_message: str | None = None) -> dict[str, object]:
        item_id = uuid4()
        with connection() as conn:
            conn.execute(
                """INSERT INTO import_batch_items
                   (id, batch_id, ingestion_job_id, existing_ingestion_job_id, source_relative_path,
                    source_file_name, source_file_type, source_sha256, source_size_bytes, source_file_path,
                    outcome, error_code, error_message, processing_plan)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (item_id, UUID(batch_id), UUID(ingestion_job_id) if ingestion_job_id else None,
                 UUID(existing_ingestion_job_id) if existing_ingestion_job_id else None, source_relative_path,
                 source_file_name, source_file_type, source_sha256, source_size_bytes, source_file_path, outcome,
                 error_code, error_message, Jsonb(processing_plan or {})),
            )
            if ingestion_job_id:
                conn.execute(
                    """UPDATE import_batches SET project_id = (
                           SELECT d.project_id FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id
                           WHERE j.id = %s), updated_at = NOW() WHERE id = %s""",
                    (UUID(ingestion_job_id), UUID(batch_id)),
                )
            conn.commit()
        return self.get_item_by_path(batch_id=batch_id, source_relative_path=source_relative_path) or {}

    def update_item(self, item_id: str, *, outcome: str | None = None,
                    processing_plan: dict[str, object] | None = None, error_code: str | None = None,
                    error_message: str | None = None, clear_error: bool = False) -> None:
        fields, params = ["updated_at = NOW()"], []
        if outcome is not None:
            fields.append("outcome = %s"); params.append(outcome)
        if processing_plan is not None:
            fields.append("processing_plan = %s"); params.append(Jsonb(processing_plan))
        if error_code is not None:
            fields.append("error_code = %s"); params.append(error_code)
        if error_message is not None:
            fields.append("error_message = %s"); params.append(error_message[:4000])
        if clear_error:
            fields.append("error_code = NULL")
            fields.append("error_message = NULL")
        params.append(UUID(item_id))
        with connection() as conn:
            conn.execute(f"UPDATE import_batch_items SET {', '.join(fields)} WHERE id = %s", tuple(params))
            conn.commit()
    def add_task(self, *, batch_id: str, item_id: str, task_kind: str, ingestion_job_id: str | None,
                 source_ingestion_job_id: str | None, file_name: str, file_type: str, source_sha256: str,
                 source_size_bytes: int, external_target: str, details: dict[str, object],
                 page_start: int | None = None, page_end: int | None = None) -> dict[str, object]:
        task_id = uuid4()
        with connection() as conn:
            row = conn.execute(
                """INSERT INTO import_batch_tasks
                   (id, batch_id, item_id, task_kind, ingestion_job_id, source_ingestion_job_id,
                    file_name, file_type, source_sha256, source_size_bytes, page_start, page_end,
                    external_target, status, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'awaiting_approval', %s)
                   ON CONFLICT (batch_id, task_kind, ingestion_job_id) DO UPDATE
                   SET page_start = EXCLUDED.page_start, page_end = EXCLUDED.page_end,
                       details = EXCLUDED.details, updated_at = NOW() RETURNING id""",
                (task_id, UUID(batch_id), UUID(item_id), task_kind, UUID(ingestion_job_id) if ingestion_job_id else None,
                 UUID(source_ingestion_job_id) if source_ingestion_job_id else None, file_name, file_type,
                 source_sha256, source_size_bytes, page_start, page_end, external_target, Jsonb(details)),
            ).fetchone()
            conn.commit()
        return {"id": str(row["id"])}

    def list_tasks(self, batch_id: str) -> list[dict[str, object]]:
        with connection() as conn:
            rows = conn.execute(
                """SELECT t.id, t.batch_id, t.item_id, t.task_kind, t.ingestion_job_id,
                          t.visual_analysis_job_id, t.source_ingestion_job_id, t.file_name, t.file_type,
                          t.source_sha256, t.source_size_bytes, t.page_start, t.page_end, t.external_target,
                          t.status, t.details, t.created_at, t.updated_at, j.status AS ingestion_status,
                          v.status AS visual_analysis_status, v.error_code AS visual_analysis_error_code,
                          j.error_code AS ingestion_error_code
                   FROM import_batch_tasks t LEFT JOIN ingestion_jobs j ON j.id = t.ingestion_job_id
                   LEFT JOIN visual_analysis_jobs v ON v.id = t.visual_analysis_job_id
                   WHERE t.batch_id = %s ORDER BY t.created_at ASC, t.id ASC""",
                (UUID(batch_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_page_review(self, *, batch_id: str, item_id: str, source_page_number: int,
                        preview_file_path: str, preview_sha256: str, preview_content_type: str,
                        preview_size_bytes: int, preview_width: int, preview_height: int,
                        selected_processor: str = "mineru") -> dict[str, object]:
        review_id = uuid4()
        with connection() as conn:
            row = conn.execute(
                """INSERT INTO import_batch_page_reviews
                   (id, batch_id, item_id, source_page_number, preview_file_path, preview_sha256,
                    preview_content_type, preview_size_bytes, preview_width, preview_height, selected_processor)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (batch_id, item_id, source_page_number) DO UPDATE
                   SET preview_file_path = EXCLUDED.preview_file_path, preview_sha256 = EXCLUDED.preview_sha256,
                       preview_content_type = EXCLUDED.preview_content_type, preview_size_bytes = EXCLUDED.preview_size_bytes,
                       preview_width = EXCLUDED.preview_width, preview_height = EXCLUDED.preview_height,
                       updated_at = NOW()
                   RETURNING id""",
                (review_id, UUID(batch_id), UUID(item_id), source_page_number, preview_file_path, preview_sha256,
                 preview_content_type, preview_size_bytes, preview_width, preview_height, selected_processor),
            ).fetchone()
            conn.commit()
        return self.get_page_review(batch_id=batch_id, page_review_id=str(row["id"])) or {}

    def list_page_reviews(self, batch_id: str) -> list[dict[str, object]]:
        with connection() as conn:
            rows = conn.execute(
                """SELECT r.id, r.batch_id, r.item_id, r.source_page_number, r.preview_file_path,
                          r.preview_sha256, r.preview_content_type, r.preview_size_bytes, r.preview_width,
                          r.preview_height, r.selected_processor, r.review_status, r.mineru_task_id,
                          r.vision_task_id, r.task_status, r.error_code, r.error_message, r.created_at, r.updated_at,
                          i.source_file_name, i.source_relative_path
                   FROM import_batch_page_reviews r
                   JOIN import_batch_items i ON i.id = r.item_id
                   WHERE r.batch_id = %s
                   ORDER BY i.created_at ASC, r.source_page_number ASC""",
                (UUID(batch_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_page_review(self, *, batch_id: str, page_review_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """SELECT r.id, r.batch_id, r.item_id, r.source_page_number, r.preview_file_path,
                          r.preview_sha256, r.preview_content_type, r.preview_size_bytes, r.preview_width,
                          r.preview_height, r.selected_processor, r.review_status, r.mineru_task_id,
                          r.vision_task_id, r.task_status, r.error_code, r.error_message, r.created_at, r.updated_at,
                          i.source_file_name, i.source_relative_path
                   FROM import_batch_page_reviews r JOIN import_batch_items i ON i.id = r.item_id
                   WHERE r.batch_id = %s AND r.id = %s""",
                (UUID(batch_id), UUID(page_review_id)),
            ).fetchone()
        return dict(row) if row else None

    def update_page_reviews(self, *, batch_id: str, page_review_ids: list[str], selected_processor: str) -> None:
        if not page_review_ids:
            return
        with connection() as conn:
            conn.execute(
                """UPDATE import_batch_page_reviews SET selected_processor = %s, review_status = 'pending',
                   updated_at = NOW() WHERE batch_id = %s AND id = ANY(%s)""",
                (selected_processor, UUID(batch_id), [UUID(value) for value in page_review_ids]),
            )
            conn.commit()

    def confirm_page_reviews(self, *, batch_id: str) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE import_batch_page_reviews SET review_status = 'confirmed',
                   task_status = CASE WHEN task_status = 'pending' THEN 'awaiting_approval' ELSE task_status END,
                   updated_at = NOW() WHERE batch_id = %s""",
                (UUID(batch_id),),
            )
            conn.commit()

    def link_page_review_task(self, *, page_review_id: str, processor: str, task_id: str) -> None:
        field = "mineru_task_id" if processor == "mineru" else "vision_task_id"
        with connection() as conn:
            conn.execute(
                f"UPDATE import_batch_page_reviews SET {field} = %s, task_status = 'awaiting_approval', updated_at = NOW() WHERE id = %s",
                (UUID(task_id), UUID(page_review_id)),
            )
            conn.commit()

    def set_page_review_task_state(self, *, task_id: str, status: str, error_code: str | None = None,
                                   error_message: str | None = None) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE import_batch_page_reviews SET task_status = %s, error_code = %s, error_message = %s,
                   updated_at = NOW() WHERE mineru_task_id = %s OR vision_task_id = %s""",
                (status, error_code, error_message, UUID(task_id), UUID(task_id)),
            )
            conn.commit()

    def delete_awaiting_approval_tasks(self, *, batch_id: str) -> None:
        """Remove only tasks generated from PDF page reviews before they are queued.

        A batch can also contain independently prepared image/PPT tasks.  Reopening a
        PDF review must not silently discard those unrelated approval tasks.
        """
        with connection() as conn:
            conn.execute(
                """DELETE FROM import_batch_tasks
                   WHERE batch_id = %s AND status = 'awaiting_approval' AND id IN (
                       SELECT mineru_task_id FROM import_batch_page_reviews
                       WHERE batch_id = %s AND mineru_task_id IS NOT NULL
                       UNION
                       SELECT vision_task_id FROM import_batch_page_reviews
                       WHERE batch_id = %s AND vision_task_id IS NOT NULL
                   )""",
                (UUID(batch_id), UUID(batch_id), UUID(batch_id)),
            )
            conn.execute(
                """UPDATE import_batch_page_reviews SET mineru_task_id = NULL, vision_task_id = NULL,
                   task_status = 'pending', review_status = 'pending', error_code = NULL, error_message = NULL,
                   updated_at = NOW() WHERE batch_id = %s""",
                (UUID(batch_id),),
            )
            conn.commit()

    def set_processor_approvals(self, *, batch_id: str, approve_mineru: bool, approve_vision: bool) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE import_batches
                   SET mineru_approved = mineru_approved OR %s,
                       vision_approved = vision_approved OR %s,
                       updated_at = NOW()
                   WHERE id = %s""",
                (approve_mineru, approve_vision, UUID(batch_id)),
            )
            conn.commit()

    def find_task_by_detail(self, *, batch_id: str, key: str, value: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """SELECT t.id, t.batch_id, t.item_id, t.task_kind, t.ingestion_job_id,
                          t.visual_analysis_job_id, t.source_ingestion_job_id, t.file_name, t.file_type,
                          t.source_sha256, t.source_size_bytes, t.page_start, t.page_end, t.external_target,
                          t.status, t.details, t.created_at, t.updated_at
                   FROM import_batch_tasks t
                   WHERE t.batch_id = %s AND t.details ->> %s = %s
                   ORDER BY t.created_at ASC LIMIT 1""",
                (UUID(batch_id), key, value),
            ).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id: str, *, status: str, visual_analysis_job_id: str | None = None,
                    details: dict[str, object] | None = None) -> None:
        fields, params = ["status = %s", "updated_at = NOW()"], [status]
        if visual_analysis_job_id is not None:
            fields.append("visual_analysis_job_id = %s"); params.append(UUID(visual_analysis_job_id))
        if details is not None:
            fields.append("details = %s"); params.append(Jsonb(details))
        params.append(UUID(task_id))
        with connection() as conn:
            conn.execute(f"UPDATE import_batch_tasks SET {', '.join(fields)} WHERE id = %s", tuple(params))
            conn.commit()

    def schedule_approved_batch(self, *, batch_id: str, schedule_mode: str, scheduled_for: object) -> None:
        """Persist an already-authorized schedule before any external service is called."""
        with connection() as conn:
            conn.execute(
                """UPDATE import_batches SET status = 'scheduled', schedule_mode = %s, scheduled_for = %s,
                   schedule_requested_at = NOW(), approved_at = COALESCE(approved_at, NOW()),
                   error_code = NULL, error_message = NULL, updated_at = NOW() WHERE id = %s""",
                (schedule_mode, scheduled_for, UUID(batch_id)),
            )
            conn.commit()

    def claim_due_scheduled_batches(self, *, limit: int = 20) -> list[str]:
        """Atomically claim due schedules; ``scheduling`` records are recovered after a restart."""
        with connection() as conn:
            rows = conn.execute(
                """WITH candidates AS (
                       SELECT id FROM import_batches
                       WHERE (status = 'scheduled' AND scheduled_for <= NOW()) OR status = 'scheduling'
                       ORDER BY scheduled_for ASC NULLS LAST, updated_at ASC
                       LIMIT %s FOR UPDATE SKIP LOCKED
                   )
                   UPDATE import_batches AS b SET status = 'scheduling', updated_at = NOW()
                   FROM candidates WHERE b.id = candidates.id
                   RETURNING b.id""",
                (limit,),
            ).fetchall()
            conn.commit()
        return [str(row["id"]) for row in rows]

    def set_prepared_manifest(self, *, batch_id: str, manifest: dict[str, object], manifest_sha256: str,
                              status: str = "awaiting_approval") -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE import_batches SET status = %s, manifest = %s, manifest_sha256 = %s,
                   error_code = NULL, error_message = NULL, prepared_at = NOW(), updated_at = NOW() WHERE id = %s""",
                (status, Jsonb(manifest), manifest_sha256, UUID(batch_id)),
            )
            conn.commit()

    def set_status(self, batch_id: str, *, status: str, error_code: str | None = None, error_message: str | None = None) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE import_batches SET status = %s, error_code = %s, error_message = %s,
                   approved_at = CASE WHEN %s = 'queued' THEN COALESCE(approved_at, NOW()) ELSE approved_at END,
                   completed_at = CASE WHEN %s IN ('succeeded', 'partially_succeeded', 'failed') THEN NOW() ELSE completed_at END,
                   updated_at = NOW() WHERE id = %s""",
                (status, error_code, error_message[:4000] if error_message else None, status, status, UUID(batch_id)),
            )
            conn.commit()


