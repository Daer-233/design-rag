"""PostgreSQL repositories for source-of-truth RAG records."""

from dataclasses import dataclass
from pathlib import Path
from hashlib import sha256
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.clients.inference import ModelIdentity
from app.db.connection import connection


@dataclass(frozen=True)
class AuthorityChunk:
    chunk_id: str
    document_id: str
    project_id: str
    content: str
    chunk_level: str
    parent_chunk_id: str | None
    access_scope: dict[str, object]
    review_status: str
    source_confidence: float
    metadata: dict[str, object]
    file_type: str
    chunk_type: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class IngestionAuthorityResult:
    project_id: str
    document_id: str
    document_version_id: str
    model_version_id: str
    chunks: list[AuthorityChunk]


def _as_json(value: object) -> Jsonb:
    return Jsonb(value if isinstance(value, dict) else {})


def _content_sha256(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _document_content(chunks: list[object]) -> str:
    values: list[str] = []
    for raw_parent in chunks:
        if not isinstance(raw_parent, dict):
            continue
        values.append(str(raw_parent["content"]))
        for raw_child in raw_parent.get("children", []):
            if isinstance(raw_child, dict):
                values.append(str(raw_child["content"]))
    return "\n".join(values)


class RagRepository:
    def store_document(
        self,
        *,
        payload: dict[str, object],
        embedding_model: ModelIdentity,
        provider_name: str,
        collection_name: str,
    ) -> IngestionAuthorityResult:
        project = payload["project"]
        document = payload["document"]
        parser = payload["parser"]
        chunks = payload["chunks"]
        assert isinstance(project, dict) and isinstance(document, dict) and isinstance(parser, dict)
        assert isinstance(chunks, list)

        with connection() as conn:
            project_id = self._upsert_project(conn, project)
            document_id = self._upsert_document(conn, project_id, document, chunks)
            version_id = uuid4()
            version_no = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no FROM document_versions WHERE document_id = %s",
                (document_id,),
            ).fetchone()["version_no"]
            conn.execute(
                """
                INSERT INTO document_versions
                (id, document_id, version_no, parser_provider, parser_model, parser_version,
                 parsed_content_uri, parsed_payload, source_confidence, source_sha256,
                 source_storage_key, source_file_size_bytes, parse_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version_id,
                    document_id,
                    version_no,
                    parser["provider"],
                    parser["model"],
                    parser.get("version"),
                    parser.get("parsed_content_uri"),
                    _as_json(parser.get("parsed_payload", {})),
                    float(parser.get("source_confidence", 1.0)),
                    parser.get("source_sha256"),
                    parser.get("source_storage_key"),
                    parser.get("source_file_size_bytes"),
                    str(parser.get("parse_status", "materialized")),
                ),
            )
            model_version_id = self._upsert_model_version(conn, embedding_model)
            authority_chunks: list[AuthorityChunk] = []
            document_scope = document.get("access_scope", {})
            assert isinstance(document_scope, dict)

            for raw_parent in chunks:
                assert isinstance(raw_parent, dict)
                parent_id = uuid4()
                parent_scope = raw_parent.get("access_scope") or document_scope
                assert isinstance(parent_scope, dict)
                parent = self._chunk_values(raw_parent, fallback=None)
                self._insert_chunk(
                    conn=conn,
                    chunk_id=parent_id,
                    version_id=version_id,
                    raw_chunk=parent,
                    chunk_level="parent",
                    parent_chunk_id=None,
                    access_scope=parent_scope,
                    model_version_id=model_version_id,
                )
                self._create_index_record(
                    conn, parent_id, provider_name, collection_name, model_version_id
                )
                authority_chunks.append(
                    self._authority_chunk(
                        chunk_id=parent_id,
                        document_id=document_id,
                        project_id=project_id,
                        content=str(parent["content"]),
                        chunk_level="parent",
                        parent_chunk_id=None,
                        access_scope=parent_scope,
                        raw_chunk=parent,
                        file_type=str(document["file_type"]),
                    )
                )

                # A missing children list remains backward-compatible: create one child
                # from the parent so new retrieval always has child-sized candidates.
                children = raw_parent.get("children") or [self._legacy_child(parent)]
                assert isinstance(children, list)
                for raw_child in children:
                    assert isinstance(raw_child, dict)
                    child = self._chunk_values(raw_child, fallback=parent)
                    child_id = uuid4()
                    self._insert_chunk(
                        conn=conn,
                        chunk_id=child_id,
                        version_id=version_id,
                        raw_chunk=child,
                        chunk_level="child",
                        parent_chunk_id=parent_id,
                        access_scope=parent_scope,
                        model_version_id=model_version_id,
                    )
                    self._create_index_record(
                        conn, child_id, provider_name, collection_name, model_version_id
                    )
                    authority_chunks.append(
                        self._authority_chunk(
                            chunk_id=child_id,
                            document_id=document_id,
                            project_id=project_id,
                            content=str(child["content"]),
                            chunk_level="child",
                            parent_chunk_id=str(parent_id),
                            access_scope=parent_scope,
                            raw_chunk=child,
                            file_type=str(document["file_type"]),
                        )
                    )
            conn.commit()
        return IngestionAuthorityResult(
            project_id=str(project_id),
            document_id=str(document_id),
            document_version_id=str(version_id),
            model_version_id=str(model_version_id),
            chunks=authority_chunks,
        )

    @staticmethod
    def _legacy_child(parent: dict[str, object]) -> dict[str, object]:
        return {
            "ordinal": 0,
            "content": parent["content"],
            "token_count": parent.get("token_count"),
            "chunk_type": parent.get("chunk_type", "text"),
            "metadata": parent.get("metadata", {}),
            "page_start": parent.get("page_start"),
            "page_end": parent.get("page_end"),
            "section_path": parent.get("section_path"),
            "source_locator": parent.get("source_locator", {}),
            "review_status": parent.get("review_status", "pending"),
            "source_confidence": parent.get("source_confidence", 1.0),
        }

    @staticmethod
    def _chunk_values(raw_chunk: dict[str, object], fallback: dict[str, object] | None) -> dict[str, object]:
        def inherited(key: str, default: object = None) -> object:
            value = raw_chunk.get(key)
            if value is not None:
                return value
            return fallback.get(key, default) if fallback else default

        source_locator = inherited("source_locator", {})
        assert isinstance(source_locator, dict)
        page = source_locator.get("page")
        return {
            "ordinal": int(raw_chunk["ordinal"]),
            "content": str(raw_chunk["content"]),
            "token_count": inherited("token_count"),
            "chunk_type": str(inherited("chunk_type", "text")),
            "metadata": inherited("metadata", {}),
            "page_start": inherited("page_start") or page,
            "page_end": inherited("page_end") or page,
            "section_path": inherited("section_path"),
            "source_locator": source_locator,
            "review_status": str(inherited("review_status", "pending")),
            "source_confidence": float(inherited("source_confidence", 1.0)),
        }

    @staticmethod
    def _insert_chunk(
        *,
        conn: object,
        chunk_id: UUID,
        version_id: UUID,
        raw_chunk: dict[str, object],
        chunk_level: str,
        parent_chunk_id: UUID | None,
        access_scope: dict[str, object],
        model_version_id: UUID,
    ) -> None:
        metadata = raw_chunk["metadata"]
        source_locator = raw_chunk["source_locator"]
        assert isinstance(metadata, dict) and isinstance(source_locator, dict)
        content = str(raw_chunk["content"])
        conn.execute(
            """
            INSERT INTO chunks
            (id, document_version_id, chunk_ordinal, chunk_level, parent_chunk_id,
             content, content_sha256, token_count, chunk_type, metadata, page_start,
             page_end, section_path, source_locator, access_scope, review_status,
             source_confidence, embedding_model_version_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                chunk_id,
                version_id,
                raw_chunk["ordinal"],
                chunk_level,
                parent_chunk_id,
                content,
                _content_sha256(content),
                raw_chunk["token_count"],
                raw_chunk["chunk_type"],
                _as_json(metadata),
                raw_chunk["page_start"],
                raw_chunk["page_end"],
                raw_chunk["section_path"],
                _as_json(source_locator),
                _as_json(access_scope),
                raw_chunk["review_status"],
                raw_chunk["source_confidence"],
                model_version_id,
            ),
        )

    @staticmethod
    def _create_index_record(
        conn: object,
        chunk_id: UUID,
        provider_name: str,
        collection_name: str,
        model_version_id: UUID,
    ) -> None:
        conn.execute(
            """
            INSERT INTO vector_index_records
            (id, chunk_id, provider_name, collection_name, point_id,
             embedding_model_version_id, index_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            """,
            (uuid4(), chunk_id, provider_name, collection_name, str(chunk_id), model_version_id),
        )

    @staticmethod
    def _authority_chunk(
        *,
        chunk_id: UUID,
        document_id: UUID,
        project_id: UUID,
        content: str,
        chunk_level: str,
        parent_chunk_id: str | None,
        access_scope: dict[str, object],
        raw_chunk: dict[str, object],
        file_type: str,
    ) -> AuthorityChunk:
        metadata = raw_chunk["metadata"]
        assert isinstance(metadata, dict)
        return AuthorityChunk(
            chunk_id=str(chunk_id),
            document_id=str(document_id),
            project_id=str(project_id),
            content=content,
            chunk_level=chunk_level,
            parent_chunk_id=parent_chunk_id,
            access_scope=access_scope,
            review_status=str(raw_chunk["review_status"]),
            source_confidence=float(raw_chunk["source_confidence"]),
            metadata=metadata,
            file_type=file_type,
            chunk_type=str(raw_chunk["chunk_type"]),
            page_start=raw_chunk.get("page_start"),
            page_end=raw_chunk.get("page_end"),
        )

    def set_model_dense_dimension(self, model_version_id: str, dense_dimension: int) -> None:
        with connection() as conn:
            conn.execute(
                "UPDATE model_versions SET dense_dimension = %s WHERE id = %s",
                (dense_dimension, UUID(model_version_id)),
            )
            conn.commit()

    def mark_index_status(self, chunk_ids: list[str], *, status: str, error: str | None = None) -> None:
        ids = [UUID(chunk_id) for chunk_id in chunk_ids]
        if not ids:
            return
        with connection() as conn:
            conn.execute(
                """
                UPDATE vector_index_records
                SET index_status = %s,
                    indexed_at = CASE WHEN %s = 'indexed' THEN NOW() ELSE indexed_at END,
                    last_error = %s,
                    updated_at = NOW()
                WHERE chunk_id = ANY(%s)
                """,
                (status, status, error, ids),
            )
            conn.commit()

    def fetch_chunks(self, chunk_ids: list[str]) -> dict[str, dict[str, object]]:
        ids = [UUID(chunk_id) for chunk_id in chunk_ids]
        if not ids:
            return {}
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.chunk_level, c.parent_chunk_id, c.chunk_type,
                       c.content, c.metadata AS chunk_metadata, c.source_locator,
                       c.access_scope AS chunk_access_scope, c.review_status,
                       c.source_confidence, c.section_path, c.page_start, c.page_end,
                       parent.id AS parent_id, parent.content AS parent_content,
                       parent.metadata AS parent_metadata, parent.source_locator AS parent_source_locator,
                       parent.review_status AS parent_review_status,
                       parent.source_confidence AS parent_source_confidence,
                       parent.section_path AS parent_section_path,
                       parent.page_start AS parent_page_start, parent.page_end AS parent_page_end,
                       parent.access_scope AS parent_access_scope,
                       d.id AS document_id, d.file_name, d.file_type, d.source_uri,
                       d.metadata AS document_metadata, d.access_scope AS document_access_scope,
                       p.id AS project_id, p.name AS project_name, p.external_key AS project_external_key
                FROM chunks c
                JOIN document_versions dv ON dv.id = c.document_version_id
                JOIN documents d ON d.id = dv.document_id
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN chunks parent ON parent.id = c.parent_chunk_id
                WHERE c.id = ANY(%s)
                """,
                (ids,),
            ).fetchall()
        return {str(row["chunk_id"]): dict(row) for row in rows}

    def list_retrieval_image_assets(
        self,
        chunk_ids: list[str],
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Find image assets directly or indirectly related to retrieved chunks.

        The relation is deliberately resolved in PostgreSQL: direct child/parent links
        win over same-version/same-page fallback, and the project constraint is applied
        before rows leave the database. This prevents image leakage across projects.
        """
        ids = [UUID(str(chunk_id)) for chunk_id in chunk_ids if chunk_id]
        if not ids:
            return []
        clauses = [
            "(ia.child_chunk_id = ANY(%s) OR ia.parent_chunk_id = ANY(%s) OR EXISTS ("
            "SELECT 1 FROM chunks hit WHERE hit.id = ANY(%s) "
            "AND (hit.document_version_id = ia.document_version_id OR ("
            "COALESCE(ia.metadata->>'asset_type', '') = 'vision_source' AND EXISTS ("
            "SELECT 1 FROM document_versions hit_version "
            "WHERE hit_version.id = hit.document_version_id "
            "AND hit_version.document_id = ia.document_id))) "
            "AND (hit.page_start IS NULL OR ia.page_number IS NULL OR "
            "ia.page_number BETWEEN hit.page_start AND COALESCE(hit.page_end, hit.page_start))"
            "))",
            "d.status = 'active'",
        ]
        params: list[object] = [ids, ids, ids]
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(UUID(project_id))
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT ia.id, ia.document_id, ia.document_version_id, ia.document_asset_id,
                       ia.parent_chunk_id, ia.child_chunk_id, ia.page_number, ia.bbox,
                       ia.image_hash, ia.image_classification, ia.processing_status,
                       ia.source_storage_key, ia.derivative_storage_key, ia.adjacent_text,
                       ia.caption_text, ia.table_context, ia.metadata, ia.review_status,
                       ia.vision_status, ia.vision_analysis,
                       da.file_name AS asset_file_name, da.storage_path, da.content_type,
                       da.content_sha256, da.size_bytes,
                       d.file_name, d.file_type, p.id AS project_id, p.name AS project_name,
                       CASE
                         WHEN ia.child_chunk_id = ANY(%s) THEN 0
                         WHEN ia.parent_chunk_id = ANY(%s) THEN 1
                         ELSE 2
                       END AS relation_rank
                FROM image_assets ia
                JOIN documents d ON d.id = ia.document_id
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN document_assets da ON da.id = ia.document_asset_id
                WHERE {' AND '.join(clauses)}
                ORDER BY relation_rank, ia.page_number NULLS LAST, ia.id
                LIMIT %s
                """,
                (ids, ids, *params, max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_image_asset(self, asset_id: str, *, project_id: str | None = None) -> dict[str, object] | None:
        """Return one active image asset with its controlled local storage path."""
        clauses = ["ia.id = %s", "d.status = 'active'"]
        params: list[object] = [UUID(asset_id)]
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(UUID(project_id))
        with connection() as conn:
            row = conn.execute(
                f"""
                SELECT ia.id, ia.document_id, ia.document_version_id, ia.page_number, ia.bbox,
                       ia.image_hash, ia.image_classification, ia.processing_status,
                       ia.source_storage_key, ia.derivative_storage_key, ia.metadata,
                       ia.vision_status, ia.vision_analysis,
                       da.file_name AS asset_file_name, da.storage_path, da.content_type,
                       da.content_sha256, da.size_bytes,
                       d.file_name, d.file_type, p.id AS project_id, p.name AS project_name
                FROM image_assets ia
                JOIN documents d ON d.id = ia.document_id
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN document_assets da ON da.id = ia.document_asset_id
                WHERE {' AND '.join(clauses)}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return dict(row) if row else None

    def list_document_page_image_assets(
        self, document_id: str, page_number: int, *, project_id: str | None = None
    ) -> list[dict[str, object]]:
        clauses = ["ia.document_id = %s", "ia.page_number = %s", "d.status = 'active'"]
        params: list[object] = [UUID(document_id), int(page_number)]
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(UUID(project_id))
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT ia.id, ia.document_id, ia.document_version_id, ia.page_number, ia.bbox,
                       ia.image_hash, ia.image_classification, ia.processing_status,
                       ia.metadata, da.file_name AS asset_file_name, da.storage_path,
                       da.content_type, da.size_bytes, d.file_name, d.file_type,
                       p.id AS project_id, p.name AS project_name
                FROM image_assets ia
                JOIN documents d ON d.id = ia.document_id
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN document_assets da ON da.id = ia.document_asset_id
                WHERE {' AND '.join(clauses)}
                ORDER BY ia.id
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def backfill_source_image_assets(
        self,
        *,
        project_id: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, object]]:
        """Create one traceable visual asset for legacy standalone image documents.

        Older direct-Vision imports predate ``image_assets``. Their text chunks remain
        valid, but no controlled visual reference can be returned with a search. This
        idempotent backfill only writes asset metadata: it never copies, moves, or
        deletes an archived original image.
        """
        clauses = [
            "d.status = 'active'",
            "LOWER(d.file_type) IN ('png', 'jpg', 'jpeg', 'webp')",
            "NOT EXISTS (SELECT 1 FROM image_assets existing WHERE existing.document_id = d.id)",
            "d.source_uri IS NOT NULL",
        ]
        params: list[object] = []
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(UUID(project_id))
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT d.id AS document_id, d.project_id, d.file_name, d.source_uri,
                       d.content_sha256, d.metadata, dv.id AS document_version_id
                FROM documents d
                JOIN document_versions dv ON dv.document_id = d.id
                WHERE {' AND '.join(clauses)}
                  AND dv.version_no = (
                    SELECT MAX(current_version.version_no)
                    FROM document_versions current_version
                    WHERE current_version.document_id = d.id
                  )
                ORDER BY d.created_at, d.id
                LIMIT %s
                """,
                (*params, max(1, min(int(limit), 100_000))),
            ).fetchall()
            created: list[dict[str, object]] = []
            for row in rows:
                metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
                visual = metadata.get("latest_visual_analysis")
                visual = visual if isinstance(visual, dict) else {}
                visual_status = "succeeded" if visual.get("status") == "succeeded" else None
                image_asset_id = uuid4()
                conn.execute(
                    """INSERT INTO image_assets
                       (id, document_id, document_version_id, page_number, bbox,
                        image_hash, image_classification, processing_status,
                        source_storage_key, adjacent_text, caption_text, table_context,
                        metadata, review_status, vision_status, vision_analysis)
                       VALUES (%s, %s, %s, NULL, '{}'::jsonb,
                               %s, 'rendering', %s,
                               %s, '', '', '{}'::jsonb,
                               %s, 'confirmed', %s, %s)""",
                    (
                        image_asset_id, row["document_id"], row["document_version_id"],
                        row["content_sha256"],
                        "vision_completed" if visual_status else "metadata_saved",
                        row["source_uri"],
                        _as_json({
                            "asset_type": "vision_source",
                            "asset_ref": f"source-image:{row['document_id']}",
                            "source": "legacy_image_document_backfill",
                        }),
                        visual_status,
                        _as_json(visual) if visual_status else None,
                    ),
                )
                created.append({
                    "id": str(image_asset_id),
                    "document_id": str(row["document_id"]),
                    "project_id": str(row["project_id"]),
                    "file_name": str(row["file_name"]),
                })
            conn.commit()
        return created
    def list_project_material_evidence(self, project_id: str, *, limit: int = 1000) -> list[dict[str, object]]:
        """Return local document-derived material candidates for conservative Vision matching."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.content, c.chunk_type, c.metadata,
                       c.page_start, c.page_end, c.section_path,
                       d.id AS document_id, d.file_name, d.file_type
                FROM chunks c
                JOIN document_versions dv ON dv.id = c.document_version_id
                JOIN documents d ON d.id = dv.document_id
                WHERE d.project_id = %s
                  AND d.status = 'active'
                  AND dv.version_no = (
                      SELECT MAX(latest.version_no) FROM document_versions latest
                      WHERE latest.document_id = d.id
                  )
                  AND c.chunk_type IN ('material_item', 'material_swatch', 'table', 'document_text')
                  AND COALESCE(c.metadata->>'content_origin', 'document_text') <> 'vision_generated'
                ORDER BY
                  CASE c.chunk_type WHEN 'material_item' THEN 0 WHEN 'material_swatch' THEN 1 WHEN 'table' THEN 2 ELSE 3 END,
                  d.file_name, c.page_start NULLS LAST, c.id
                LIMIT %s
                """,
                (UUID(project_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def project_exists(self, project_id: str) -> bool:
        with connection() as conn:
            row = conn.execute("SELECT 1 FROM projects WHERE id = %s", (UUID(project_id),)).fetchone()
        return row is not None

    @staticmethod
    def _upsert_project(conn: object, project: dict[str, object]) -> UUID:
        project_id = uuid4()
        row = conn.execute(
            """
            INSERT INTO projects (id, external_key, name, metadata)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (external_key) DO UPDATE
            SET name = EXCLUDED.name, metadata = EXCLUDED.metadata, updated_at = NOW()
            RETURNING id
            """,
            (project_id, project["external_key"], project["name"], _as_json(project.get("metadata", {}))),
        ).fetchone()
        return row["id"]

    @staticmethod
    def _upsert_document(conn: object, project_id: UUID, document: dict[str, object], chunks: list[object]) -> UUID:
        document_id = uuid4()
        document_hash = _content_sha256(_document_content(chunks))
        row = conn.execute(
            """
            INSERT INTO documents
            (id, project_id, external_key, source_uri, file_name, file_type, content_sha256,
             metadata, access_scope, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (project_id, external_key) DO UPDATE
            SET source_uri = EXCLUDED.source_uri, file_name = EXCLUDED.file_name,
                file_type = EXCLUDED.file_type, content_sha256 = EXCLUDED.content_sha256,
                metadata = EXCLUDED.metadata, access_scope = EXCLUDED.access_scope,
                updated_at = NOW()
            RETURNING id
            """,
            (
                document_id,
                project_id,
                document["external_key"],
                document["source_uri"],
                document["file_name"],
                document["file_type"],
                document_hash,
                _as_json(document.get("metadata", {})),
                _as_json(document.get("access_scope", {})),
            ),
        ).fetchone()
        return row["id"]

    @staticmethod
    def _upsert_model_version(conn: object, identity: ModelIdentity) -> UUID:
        model_version_id = uuid4()
        row = conn.execute(
            """
            INSERT INTO model_versions
            (id, kind, provider, model_id, source_revision, artifact_sha256, config)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (kind, provider, model_id, source_revision, artifact_sha256) DO UPDATE
            SET config = EXCLUDED.config
            RETURNING id
            """,
            (
                model_version_id,
                identity.kind,
                identity.provider,
                identity.model_id,
                identity.source_revision or "",
                identity.artifact_sha256 or "",
                _as_json(identity.config),
            ),
        ).fetchone()
        return row["id"]

    def create_ingestion_job(
        self,
        *,
        project: dict[str, object],
        document: dict[str, object],
        provider_name: str,
        source_sha256: str,
        source_file_name: str,
        source_file_path: str,
        source_content_type: str | None,
        source_size_bytes: int,
        processing_route: str,
        route_reason: str,
        trace_id: str,
    ) -> dict[str, object]:
        with connection() as conn:
            project_id = self._upsert_project(conn, project)
            document_id = self._upsert_document(conn, project_id, document, [])
            job_id = uuid4()
            conn.execute(
                """
                INSERT INTO ingestion_jobs
                (id, document_id, provider_name, status, source_sha256, source_file_name,
                 source_file_path, source_content_type, source_size_bytes, processing_route, route_reason, trace_id)
                VALUES (%s, %s, %s, 'created', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    document_id,
                    provider_name,
                    source_sha256,
                    source_file_name,
                    source_file_path,
                    source_content_type,
                    source_size_bytes,
                    processing_route,
                    route_reason,
                    trace_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO document_assets
                (id, document_id, ingestion_job_id, asset_kind, file_name, storage_path,
                 content_type, content_sha256, size_bytes)
                VALUES (%s, %s, %s, 'original', %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    document_id,
                    job_id,
                    source_file_name,
                    source_file_path,
                    source_content_type,
                    source_sha256,
                    source_size_bytes,
                ),
            )
            conn.commit()
        return self.get_ingestion_job(str(job_id)) or {}

    def get_ingestion_job(self, job_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT j.id, j.document_id, j.document_version_id, j.provider_name, j.status,
                       j.source_sha256, j.source_file_name, j.source_file_path,
                       j.source_content_type, j.source_size_bytes, j.processing_route, j.route_reason, j.provider_task_id,
                       j.provider_batch_id, j.provider_request, j.provider_response,
                       j.result_path, j.retry_count, j.error_code, j.error_message,
                       j.trace_id, j.created_at, j.submitted_at, j.completed_at, j.updated_at,
                       d.project_id, d.external_key AS document_external_key, d.file_name,
                       d.file_type, d.source_uri, d.metadata AS document_metadata,
                       d.access_scope AS document_access_scope,
                       p.external_key AS project_external_key, p.name AS project_name
                FROM ingestion_jobs j
                JOIN documents d ON d.id = j.document_id
                JOIN projects p ON p.id = d.project_id
                WHERE j.id = %s
                """,
                (UUID(job_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_unmaterialized_ingestion_job(self, job_id: str) -> dict[str, object]:
        """Delete one safe-to-remove local import attempt.

        Materialized records are deliberately protected: their document versions may
        have chunks and vector-index records, so deleting them here could leave the
        knowledge base inconsistent. A separate document/project deletion workflow is
        required for indexed content.
        """
        with connection() as conn:
            job = conn.execute(
                """
                SELECT id, document_id, document_version_id, status, source_file_path
                FROM ingestion_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (UUID(job_id),),
            ).fetchone()
            if not job:
                return {"id": job_id, "outcome": "skipped", "reason": "导入记录不存在。"}

            if job["status"] not in {"created", "failed", "cancelled"}:
                return {
                    "id": str(job["id"]),
                    "outcome": "skipped",
                    "reason": "仅可删除未提交或失败/已取消的导入记录；处理中和成功记录会被保留。",
                }
            if job["document_version_id"]:
                return {
                    "id": str(job["id"]),
                    "outcome": "skipped",
                    "reason": "该记录已物化为知识库文档，不能在导入记录页删除。",
                }

            visual_row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded
                FROM visual_analysis_jobs
                WHERE ingestion_job_id = %s
                """,
                (job["id"],),
            ).fetchone()
            if visual_row and int(visual_row["succeeded"] or 0):
                return {
                    "id": str(job["id"]),
                    "outcome": "skipped",
                    "reason": "该记录已有成功的图片分析结果，不能在导入记录页删除。",
                }

            conn.execute("DELETE FROM document_assets WHERE ingestion_job_id = %s", (job["id"],))
            conn.execute("DELETE FROM visual_analysis_jobs WHERE ingestion_job_id = %s", (job["id"],))
            conn.execute("DELETE FROM ingestion_jobs WHERE id = %s", (job["id"],))
            remaining = conn.execute(
                "SELECT COUNT(*) AS total FROM ingestion_jobs WHERE document_id = %s",
                (job["document_id"],),
            ).fetchone()
            versions = conn.execute(
                "SELECT COUNT(*) AS total FROM document_versions WHERE document_id = %s",
                (job["document_id"],),
            ).fetchone()
            document_deleted = False
            if int(remaining["total"] if remaining else 0) == 0 and int(versions["total"] if versions else 0) == 0:
                conn.execute("DELETE FROM documents WHERE id = %s", (job["document_id"],))
                document_deleted = True
            conn.commit()

        return {
            "id": str(job["id"]),
            "outcome": "deleted",
            "source_file_path": str(job["source_file_path"]),
            "document_deleted": document_deleted,
            "visual_jobs_deleted": int(visual_row["total"] if visual_row else 0),
        }


    def list_mineru_image_assets(self, ingestion_job_id: str, *, source_pages: list[int] | None = None) -> list[dict[str, object]]:
        """Return images materialized from one MinerU job, never from another project/job."""
        clauses = ["da.ingestion_job_id = %s", "da.asset_kind = 'image'", "da.storage_path IS NOT NULL"]
        params: list[object] = [UUID(ingestion_job_id)]
        if source_pages:
            clauses.append("ia.page_number = ANY(%s)")
            params.append(source_pages)
        with connection() as conn:
            rows = conn.execute(
                f"""SELECT ia.id, ia.document_id, ia.document_version_id, ia.document_asset_id,
                          ia.page_number, ia.bbox, ia.image_hash, ia.image_classification,
                          ia.source_storage_key, ia.derivative_storage_key, ia.adjacent_text,
                          ia.caption_text, ia.table_context, ia.metadata, ia.processing_status,
                          da.file_name, da.storage_path, da.content_type, da.content_sha256, da.size_bytes,
                          j.project_id, j.id AS ingestion_job_id
                   FROM image_assets ia
                   JOIN document_assets da ON da.id = ia.document_asset_id
                   JOIN ingestion_jobs j ON j.id = da.ingestion_job_id
                   WHERE {' AND '.join(clauses)}
                   ORDER BY ia.page_number NULLS LAST, ia.id""",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_ingestion_jobs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        project_id: str | None = None,
        status: str | None = None,
        include_superseded: bool = False,
    ) -> dict[str, object]:
        """Return the effective import record for each source file.

        A force-reprocess or an interrupted retry can leave several ingestion jobs for
        one project/source SHA-256.  The Admin list must not make a later duplicate
        queue look like the only result when an earlier job already materialized the
        document or completed visual analysis.  By default we therefore show one
        canonical row per project and source hash, preferring the newest successful
        result.  The optional audit view can include the superseded attempts.
        """
        clauses: list[str] = ["COALESCE((p.metadata ->> 'admin_hidden')::boolean, FALSE) = FALSE"]
        params: list[object] = []
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(UUID(project_id))
        if status:
            clauses.append("j.status = %s")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        visible_sql = "" if include_superseded else "WHERE duplicate_rank = 1"
        with connection() as conn:
            rows = conn.execute(
                f"""
                WITH latest_visual AS (
                    SELECT DISTINCT ON (v.ingestion_job_id)
                           v.ingestion_job_id, v.id, v.status, v.derived_document_version_id, v.error_code,
                           v.error_message, v.created_at, v.completed_at, v.updated_at
                    FROM visual_analysis_jobs v
                    ORDER BY v.ingestion_job_id, v.created_at DESC, v.id DESC
                ), job_rows AS (
                    SELECT j.id, j.document_id, j.document_version_id, j.provider_name, j.status,
                           j.source_sha256, j.source_file_name, j.source_content_type,
                           j.source_size_bytes, j.processing_route, j.route_reason,
                           j.provider_task_id, j.provider_batch_id, j.result_path,
                           j.retry_count, j.error_code, j.error_message, j.trace_id,
                           j.created_at, j.submitted_at, j.completed_at, j.updated_at,
                           d.project_id, d.external_key AS document_external_key, d.file_name,
                           d.file_type, d.source_uri,
                           d.metadata ->> 'source_document_name' AS source_document_name,
                           p.external_key AS project_external_key, p.name AS project_name,
                           visual.id AS visual_analysis_job_id,
                           visual.status AS visual_analysis_status,
                           visual.derived_document_version_id,
                           visual.completed_at AS visual_analysis_completed_at,
                           visual.error_code AS visual_analysis_error_code,
                           visual.error_message AS visual_analysis_error_message,
                           visual.created_at AS visual_analysis_created_at,
                           visual.updated_at AS visual_analysis_updated_at,
                           (j.status = 'succeeded' OR visual.status = 'succeeded')
                               AS has_successful_result
                    FROM ingestion_jobs j
                    JOIN documents d ON d.id = j.document_id
                    JOIN projects p ON p.id = d.project_id
                    LEFT JOIN latest_visual visual ON visual.ingestion_job_id = j.id
                    {where_sql}
                ), ranked AS (
                    SELECT job_rows.*,
                           COUNT(*) OVER duplicate_group AS duplicate_attempt_count,
                           COUNT(*) FILTER (WHERE has_successful_result) OVER duplicate_group
                               AS successful_attempt_count,
                           COUNT(*) FILTER (
                               WHERE NOT has_successful_result
                                 AND (status IN ('queued', 'processing', 'submitted')
                                      OR visual_analysis_status IN ('queued', 'running'))
                           ) OVER duplicate_group AS active_superseded_attempt_count,
                           ROW_NUMBER() OVER canonical_order AS duplicate_rank,
                           FIRST_VALUE(id) OVER canonical_order AS canonical_job_id
                    FROM job_rows
                    WINDOW
                        duplicate_group AS (
                            PARTITION BY project_id, source_sha256
                        ),
                        canonical_order AS (
                            PARTITION BY project_id, source_sha256
                            ORDER BY
                                CASE WHEN has_successful_result THEN 0 ELSE 1 END,
                                CASE WHEN has_successful_result
                                     THEN COALESCE(completed_at, visual_analysis_updated_at, updated_at)
                                END DESC NULLS LAST,
                                updated_at DESC,
                                created_at DESC,
                                id DESC
                        )
                ), visible_rows AS (
                    SELECT ranked.*,
                           CASE WHEN duplicate_rank = 1 THEN 'canonical'
                                ELSE 'superseded_duplicate'
                           END AS record_disposition,
                           GREATEST(duplicate_attempt_count - 1, 0) AS superseded_attempt_count
                    FROM ranked
                    {visible_sql}
                )
                SELECT visible_rows.*, COUNT(*) OVER() AS filtered_total
                FROM visible_rows
                ORDER BY
                    CASE WHEN record_disposition = 'canonical' THEN 0 ELSE 1 END,
                    created_at DESC,
                    id DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            ).fetchall()
        items = [dict(row) for row in rows]
        total = int(items[0]["filtered_total"]) if items else 0
        for item in items:
            item.pop("filtered_total", None)
        return {"items": items, "total": total}

    def list_ingestion_jobs_by_ids(self, job_ids: list[str]) -> list[dict[str, object]]:
        """Return a stable, minimal batch for Admin status polling."""
        if not job_ids:
            return []
        ids = [UUID(job_id) for job_id in job_ids]
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT j.id, j.document_id, j.document_version_id, j.provider_name, j.status,
                       j.source_file_name, j.source_content_type, j.source_size_bytes,
                       j.processing_route, j.route_reason, j.provider_task_id,
                       j.provider_batch_id, j.retry_count, j.error_code, j.error_message,
                       j.trace_id, j.created_at, j.submitted_at, j.completed_at, j.updated_at,
                       d.project_id, d.external_key AS document_external_key, d.file_name,
                       d.file_type, d.metadata ->> 'source_document_name' AS source_document_name,
                        p.external_key AS project_external_key, p.name AS project_name,
                       visual.id AS visual_analysis_job_id,
                       visual.status AS visual_analysis_status,
                       visual.derived_document_version_id,
                       visual.completed_at AS visual_analysis_completed_at,
                       visual.error_code AS visual_analysis_error_code,
                       visual.error_message AS visual_analysis_error_message,
                       visual.created_at AS visual_analysis_created_at,
                       visual.updated_at AS visual_analysis_updated_at
                FROM ingestion_jobs j
                JOIN documents d ON d.id = j.document_id
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN LATERAL (
                    SELECT v.id, v.status, v.derived_document_version_id, v.error_code, v.error_message,
                           v.created_at, v.completed_at, v.updated_at
                    FROM visual_analysis_jobs v
                    WHERE v.ingestion_job_id = j.id
                    ORDER BY v.created_at DESC
                    LIMIT 1
                ) visual ON TRUE
                WHERE j.id = ANY(%s)
                """,
                (ids,),
            ).fetchall()
        by_id = {str(row["id"]): dict(row) for row in rows}
        return [by_id[job_id] for job_id in job_ids if job_id in by_id]

    def list_visual_analysis_jobs(self, ingestion_job_id: str) -> list[dict[str, object]]:
        """List visual-analysis attempts for one import without exposing archive paths."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, ingestion_job_id, source_document_version_id, derived_document_version_id,
                       provider_name, model_id, prompt_version, status, source_sha256,
                       analysis, retry_count, cancel_requested_at, cancel_reason,
                       materialization_started_at, error_code, error_message, trace_id, created_at,
                       submitted_at, completed_at, updated_at
                FROM visual_analysis_jobs
                WHERE ingestion_job_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (UUID(ingestion_job_id),),
            ).fetchall()
        return [dict(row) for row in rows]
    def enqueue_ingestion_submission(self, job_id: str) -> dict[str, object]:
        """Mark an explicitly approved document for local background submission."""
        with connection() as conn:
            row = conn.execute(
                """UPDATE ingestion_jobs
                   SET status = 'queued', error_code = NULL, error_message = NULL,
                       completed_at = NULL, updated_at = NOW()
                   WHERE id = %s AND status IN ('created', 'failed')
                   RETURNING id""",
                (UUID(job_id),),
            ).fetchone()
            conn.commit()
        if row:
            return {"outcome": "queued", "job": self.get_ingestion_job(job_id) or {}}
        job = self.get_ingestion_job(job_id)
        if not job:
            return {"outcome": "skipped", "reason": "导入记录不存在。"}
        if job["status"] in {"queued", "processing", "submitted"}:
            return {"outcome": "already_queued", "job": job}
        return {"outcome": "skipped", "reason": f"当前状态为 {job['status']}，无需或不能再次提交。", "job": job}

    def recover_unsubmitted_ingestion_jobs(self) -> int:
        """Return interrupted local parser claims to the durable queue.

        A process restart can occur after a worker changes a job from ``queued`` to
        ``processing`` but before MinerU returns a provider task ID.  Such a job has
        not been exported yet, so moving it back to ``queued`` is safe and avoids a
        permanently stuck Admin record.  Jobs with a provider task ID are deliberately
        excluded because they must be polled rather than submitted again.
        """
        with connection() as conn:
            rows = conn.execute(
                """UPDATE ingestion_jobs
                   SET status = 'queued', updated_at = NOW()
                   WHERE status = 'processing' AND provider_task_id IS NULL
                   RETURNING id"""
            ).fetchall()
            conn.commit()
        return len(rows)

    def claim_ingestion_submission(self, job_id: str) -> bool:
        """Claim one locally queued parser submission for the background worker."""
        with connection() as conn:
            row = conn.execute(
                """UPDATE ingestion_jobs
                   SET status = 'processing', updated_at = NOW()
                   WHERE id = %s AND status = 'queued'
                   RETURNING id""",
                (UUID(job_id),),
            ).fetchone()
            conn.commit()
        return bool(row)

    def record_ingestion_submission(
        self,
        job_id: str,
        *,
        task_id: str,
        batch_id: str | None,
        provider_request: dict[str, object],
        provider_response: dict[str, object],
    ) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'submitted', provider_task_id = %s, provider_batch_id = %s,
                    provider_request = %s, provider_response = %s, error_code = NULL,
                    error_message = NULL, submitted_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (task_id, batch_id, _as_json(provider_request), _as_json(provider_response), UUID(job_id)),
            )
            conn.commit()

    def record_ingestion_poll(
        self,
        job_id: str,
        *,
        status: str,
        provider_response: dict[str, object],
        result_path: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        terminal = status in {"succeeded", "failed", "cancelled"}
        with connection() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = %s, provider_response = %s, result_path = COALESCE(%s, result_path),
                    error_code = %s, error_message = %s,
                    completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    status,
                    _as_json(provider_response),
                    result_path,
                    error_code,
                    error_message,
                    terminal,
                    UUID(job_id),
                ),
            )
            conn.commit()

    def record_ingestion_failure(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'failed', error_code = %s, error_message = %s,
                    completed_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (error_code, error_message[:4000], UUID(job_id)),
            )
            conn.commit()

    def add_document_asset(
        self,
        *,
        document_id: str,
        ingestion_job_id: str,
        asset_kind: str,
        file_name: str,
        storage_path: str,
        content_type: str | None,
        content_sha256: str | None,
        size_bytes: int | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO document_assets
                (id, document_id, ingestion_job_id, asset_kind, file_name, storage_path,
                 content_type, content_sha256, size_bytes, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    UUID(document_id),
                    UUID(ingestion_job_id),
                    asset_kind,
                    file_name,
                    storage_path,
                    content_type,
                    content_sha256,
                    size_bytes,
                    _as_json(metadata or {}),
                ),
            )
            conn.commit()

    def link_ingestion_job_document_version(self, job_id: str, document_version_id: str) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET document_version_id = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (UUID(document_version_id), UUID(job_id)),
            )
            conn.commit()



    def create_visual_analysis_job(
        self,
        *,
        ingestion_job_id: str,
        provider_name: str,
        model_id: str,
        prompt_version: str,
        provider_config: dict[str, object],
        trace_id: str,
    ) -> dict[str, object]:
        """Create a local, non-exporting visual job for backward-compatible callers."""
        source = self._validate_visual_source(ingestion_job_id)
        identity = ModelIdentity(
            kind="llm",
            provider=provider_name,
            model_id=model_id,
            source_revision="",
            artifact_sha256=None,
            config=provider_config,
        )
        with connection() as conn:
            model_version_id = self._upsert_model_version(conn, identity)
            job_id = uuid4()
            conn.execute(
                """
                INSERT INTO visual_analysis_jobs
                (id, ingestion_job_id, source_document_version_id, model_version_id,
                 provider_name, model_id, prompt_version, status, source_sha256, trace_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'created', %s, %s)
                """,
                (
                    job_id,
                    UUID(ingestion_job_id),
                    UUID(str(source["document_version_id"])) if source["document_version_id"] else None,
                    model_version_id,
                    provider_name,
                    model_id,
                    prompt_version,
                    str(source["source_sha256"]),
                    trace_id,
                ),
            )
            conn.commit()
        return self.get_visual_analysis_job(str(job_id)) or {}

    def queue_visual_analysis_for_ingestion(
        self,
        *,
        ingestion_job_id: str,
        provider_name: str,
        model_id: str,
        prompt_version: str,
        provider_config: dict[str, object],
        trace_id: str,
    ) -> dict[str, object]:
        """Atomically queue an image, reusing any eligible local attempt.

        The advisory lock plus the partial unique index guarantee that one archived
        image can have at most one externally billable active job (queued or running).
        """
        source = self._validate_visual_source(ingestion_job_id)
        identity = ModelIdentity(
            kind="llm", provider=provider_name, model_id=model_id,
            source_revision="", artifact_sha256=None, config=provider_config,
        )
        with connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"visual:{ingestion_job_id}",))
            existing = conn.execute(
                """
                SELECT id, status, retry_count
                FROM visual_analysis_jobs
                WHERE ingestion_job_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (UUID(ingestion_job_id),),
            ).fetchall()
            active = next((row for row in existing if row["status"] in {"queued", "running"}), None)
            if active:
                conn.commit()
                return {"outcome": "already_queued", "job": self.get_visual_analysis_job(str(active["id"])) or {}}
            succeeded = next((row for row in existing if row["status"] == "succeeded"), None)
            if succeeded:
                conn.commit()
                return {"outcome": "skipped", "reason": "该图片已成功完成视觉分析，无需再次外发。", "job": self.get_visual_analysis_job(str(succeeded["id"])) or {}}
            created = next((row for row in existing if row["status"] == "created"), None)
            if created:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'queued', error_code = NULL, error_message = NULL,
                           cancel_requested_at = NULL, cancel_reason = NULL,
                           materialization_started_at = NULL, completed_at = NULL, updated_at = NOW()
                       WHERE id = %s""",
                    (created["id"],),
                )
                conn.commit()
                return {"outcome": "queued", "job": self.get_visual_analysis_job(str(created["id"])) or {}}
            retryable = next((row for row in existing if row["status"] == "failed" and int(row["retry_count"] or 0) < 1), None)
            if retryable:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'queued', retry_count = retry_count + 1,
                           error_code = NULL, error_message = NULL, cancel_requested_at = NULL,
                           cancel_reason = NULL, materialization_started_at = NULL,
                           completed_at = NULL, updated_at = NOW()
                       WHERE id = %s""",
                    (retryable["id"],),
                )
                conn.commit()
                return {"outcome": "queued_retry", "job": self.get_visual_analysis_job(str(retryable["id"])) or {}}
            exhausted = next((row for row in existing if row["status"] == "failed"), None)
            if exhausted:
                conn.commit()
                return {"outcome": "skipped", "reason": "该图片已达到最多 1 次失败重试限制。", "job": self.get_visual_analysis_job(str(exhausted["id"])) or {}}

            model_version_id = self._upsert_model_version(conn, identity)
            job_id = uuid4()
            conn.execute(
                """
                INSERT INTO visual_analysis_jobs
                (id, ingestion_job_id, source_document_version_id, model_version_id,
                 provider_name, model_id, prompt_version, status, source_sha256, trace_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s)
                """,
                (
                    job_id, UUID(ingestion_job_id),
                    UUID(str(source["document_version_id"])) if source["document_version_id"] else None,
                    model_version_id, provider_name, model_id, prompt_version,
                    str(source["source_sha256"]), trace_id,
                ),
            )
            conn.commit()
        return {"outcome": "queued", "job": self.get_visual_analysis_job(str(job_id)) or {}}

    def queue_visual_analysis_job(self, job_id: str) -> dict[str, object]:
        """Queue an already-created local visual job without exporting it inline."""
        job = self.get_visual_analysis_job(job_id)
        if not job:
            raise ValueError("Visual analysis job not found")
        with connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"visual:{job['ingestion_job_id']}",))
            row = conn.execute(
                "SELECT id, status, retry_count FROM visual_analysis_jobs WHERE id = %s FOR UPDATE",
                (UUID(job_id),),
            ).fetchone()
            if not row:
                raise ValueError("Visual analysis job not found")
            active = conn.execute(
                """SELECT id FROM visual_analysis_jobs
                   WHERE ingestion_job_id = %s AND status IN ('queued', 'running') AND id <> %s
                   LIMIT 1""",
                (UUID(str(job["ingestion_job_id"])), row["id"]),
            ).fetchone()
            if active:
                conn.commit()
                return {"outcome": "already_queued", "job": self.get_visual_analysis_job(str(active["id"])) or {}}
            if row["status"] in {"queued", "running"}:
                conn.commit()
                return {"outcome": "already_queued", "job": self.get_visual_analysis_job(job_id) or {}}
            if row["status"] == "succeeded":
                conn.commit()
                return {"outcome": "skipped", "reason": "该图片已成功完成视觉分析。", "job": self.get_visual_analysis_job(job_id) or {}}
            if row["status"] == "failed" and int(row["retry_count"] or 0) >= 1:
                conn.commit()
                return {"outcome": "skipped", "reason": "该图片已达到最多 1 次失败重试限制。", "job": self.get_visual_analysis_job(job_id) or {}}
            retry_sql = ", retry_count = retry_count + 1" if row["status"] == "failed" else ""
            conn.execute(
                f"""UPDATE visual_analysis_jobs SET status = 'queued'{retry_sql},
                    error_code = NULL, error_message = NULL, cancel_requested_at = NULL,
                    cancel_reason = NULL, materialization_started_at = NULL,
                    completed_at = NULL, updated_at = NOW() WHERE id = %s""",
                (row["id"],),
            )
            conn.commit()
        return {"outcome": "queued", "job": self.get_visual_analysis_job(job_id) or {}}

    def get_visual_analysis_job(self, job_id: str) -> dict[str, object] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT v.id, v.ingestion_job_id, v.source_document_version_id,
                       v.derived_document_version_id, v.model_version_id, v.provider_name,
                       v.model_id, v.prompt_version, v.status, v.source_sha256,
                       v.provider_request, v.provider_response, v.analysis,
                       v.retry_count, v.cancel_requested_at, v.cancel_reason,
                       v.materialization_started_at, v.error_code, v.error_message, v.trace_id,
                       v.created_at, v.submitted_at, v.completed_at, v.updated_at,
                       j.document_id, j.source_file_name, j.source_file_path,
                       j.source_content_type, j.source_size_bytes,
                       d.project_id, d.external_key AS document_external_key, d.file_name,
                       d.file_type, d.source_uri, d.metadata AS document_metadata,
                       d.access_scope AS document_access_scope,
                       p.external_key AS project_external_key, p.name AS project_name
                FROM visual_analysis_jobs v
                JOIN ingestion_jobs j ON j.id = v.ingestion_job_id
                JOIN documents d ON d.id = j.document_id
                JOIN projects p ON p.id = d.project_id
                WHERE v.id = %s
                """,
                (UUID(job_id),),
            ).fetchone()
        return dict(row) if row else None

    def recover_cancel_requested_visual_analysis_jobs(self) -> int:
        """Finalize cancellation requests left running across a local worker restart.

        A running HTTP call cannot be interrupted safely, so cancellation normally takes
        effect at the materialization boundary.  After a process restart there is no
        longer an in-flight local worker to reach that boundary; keeping the row in
        ``running`` would permanently block both the UI and future explicit retries.
        Only cancellation-requested jobs that have not started materialization are
        finalized here, so no derived content can be written after cancellation.
        """
        with connection() as conn:
            rows = conn.execute(
                """UPDATE visual_analysis_jobs
                   SET status = 'cancelled', error_code = 'CANCELLED_BY_USER',
                       error_message = '已取消；本地服务重启后未写入知识库。',
                       completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                   WHERE status = 'running'
                     AND cancel_requested_at IS NOT NULL
                     AND materialization_started_at IS NULL
                   RETURNING id"""
            ).fetchall()
            conn.commit()
        return len(rows)

    def recover_stale_running_visual_analysis_jobs(self, *, stale_after_seconds: int) -> dict[str, int]:
        """Repair visual-analysis rows stranded by a local worker interruption.

        The external image request cannot be resumed after a process exits. A stale
        row is never automatically requeued: if its derived document version was
        already written, it is finalized as succeeded; otherwise it is marked failed
        and requires an explicit, authorized retry from Admin.
        """
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")

        restored = 0
        failed = 0
        cancelled = 0
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT v.id, v.cancel_requested_at,
                       d.metadata -> 'latest_visual_analysis' AS latest_visual_analysis,
                       (
                           SELECT dv.id
                           FROM document_versions dv
                           WHERE dv.document_id = j.document_id
                             AND dv.parsed_payload ->> 'visual_analysis_job_id' = v.id::text
                           ORDER BY dv.created_at DESC, dv.id DESC
                           LIMIT 1
                       ) AS materialized_version_id
                FROM visual_analysis_jobs v
                JOIN ingestion_jobs j ON j.id = v.ingestion_job_id
                JOIN documents d ON d.id = j.document_id
                WHERE v.status = 'running'
                  AND v.updated_at <= NOW() - make_interval(secs => %s)
                FOR UPDATE OF v
                """,
                (stale_after_seconds,),
            ).fetchall()
            for row in rows:
                materialized_version_id = row["materialized_version_id"]
                if materialized_version_id is not None:
                    persisted_summary = row["latest_visual_analysis"]
                    analysis: dict[str, object] = (
                        dict(persisted_summary) if isinstance(persisted_summary, dict) else {}
                    )
                    analysis["recovery"] = {
                        "status": "recovered_from_materialized_document",
                        "derived_document_version_id": str(materialized_version_id),
                        "note": "检测到视觉分析结果已写入知识库，服务重启后已补齐任务状态。",
                    }
                    conn.execute(
                        """
                        UPDATE visual_analysis_jobs
                        SET status = 'succeeded', derived_document_version_id = %s,
                            analysis = CASE
                                WHEN analysis = '{}'::jsonb THEN %s
                                ELSE analysis
                            END,
                            error_code = NULL, error_message = NULL,
                            completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                        WHERE id = %s AND status = 'running'
                        """,
                        (materialized_version_id, _as_json(analysis), row["id"]),
                    )
                    restored += 1
                elif row["cancel_requested_at"] is not None:
                    conn.execute(
                        """
                        UPDATE visual_analysis_jobs
                        SET status = 'cancelled', error_code = 'CANCELLED_BY_USER',
                            error_message = '已取消；服务中断后未检测到可写入的视觉分析结果。',
                            completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                        WHERE id = %s AND status = 'running'
                        """,
                        (row["id"],),
                    )
                    cancelled += 1
                else:
                    conn.execute(
                        """
                        UPDATE visual_analysis_jobs
                        SET status = 'failed', error_code = 'VISUAL_ANALYSIS_STALE',
                            error_message = '视觉分析任务在服务中断后未恢复，且未检测到知识库写入结果；请明确重新提交。',
                            completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                        WHERE id = %s AND status = 'running'
                        """,
                        (row["id"],),
                    )
                    failed += 1
            conn.commit()
        return {"restored": restored, "failed": failed, "cancelled": cancelled}

    def list_queued_visual_analysis_jobs(self, *, limit: int) -> list[str]:
        with connection() as conn:
            rows = conn.execute(
                """SELECT id FROM visual_analysis_jobs
                   WHERE status = 'queued' ORDER BY created_at, id LIMIT %s""",
                (limit,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def claim_visual_analysis_job(self, job_id: str) -> bool:
        with connection() as conn:
            row = conn.execute(
                """UPDATE visual_analysis_jobs
                   SET status = 'running', submitted_at = COALESCE(submitted_at, NOW()),
                       updated_at = NOW()
                   WHERE id = %s AND status = 'queued'
                   RETURNING id""",
                (UUID(job_id),),
            ).fetchone()
            conn.commit()
        return bool(row)

    def request_visual_analysis_cancel(self, job_id: str) -> dict[str, object]:
        with connection() as conn:
            row = conn.execute(
                """SELECT id, status, materialization_started_at
                   FROM visual_analysis_jobs WHERE id = %s FOR UPDATE""",
                (UUID(job_id),),
            ).fetchone()
            if not row:
                return {"id": job_id, "outcome": "skipped", "reason": "图片分析任务不存在。"}
            if row["status"] in {"created", "queued"}:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'cancelled', error_code = 'CANCELLED_BY_USER',
                           error_message = '已取消；任务未发送到外部视觉模型。',
                           completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                    (row["id"],),
                )
                outcome = "cancelled"
                reason = "任务已取消，未开始的图片不会发送到外部模型。"
            elif row["status"] == "running" and row["materialization_started_at"] is None:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET cancel_requested_at = COALESCE(cancel_requested_at, NOW()),
                           cancel_reason = 'CANCELLED_BY_USER', updated_at = NOW()
                       WHERE id = %s""",
                    (row["id"],),
                )
                outcome = "cancel_requested"
                reason = "已请求取消；若外部分析已在执行，结果返回后不会写入知识库。"
            elif row["status"] == "running":
                outcome = "will_complete"
                reason = "结果已开始写入知识库，本次任务将允许完成。"
            else:
                outcome = "skipped"
                reason = f"当前状态为 {row['status']}，无需取消。"
            conn.commit()
        return {"id": str(row["id"]), "outcome": outcome, "reason": reason, "job": self.get_visual_analysis_job(str(row["id"])) or {}}

    def prepare_visual_analysis_materialization(self, job_id: str) -> bool:
        """Reserve the materialization boundary, honoring cancellation first."""
        with connection() as conn:
            row = conn.execute(
                """SELECT id, status, cancel_requested_at
                   FROM visual_analysis_jobs WHERE id = %s FOR UPDATE""",
                (UUID(job_id),),
            ).fetchone()
            if not row or row["status"] != "running":
                conn.commit()
                return False
            if row["cancel_requested_at"] is not None:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'cancelled', error_code = 'CANCELLED_BY_USER',
                           error_message = '已取消；外部分析结果未写入知识库。',
                           completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                    (row["id"],),
                )
                conn.commit()
                return False
            conn.execute(
                """UPDATE visual_analysis_jobs
                   SET materialization_started_at = NOW(), updated_at = NOW() WHERE id = %s""",
                (row["id"],),
            )
            conn.commit()
        return True

    def record_visual_analysis_success(
        self,
        job_id: str,
        *,
        provider_request: dict[str, object],
        provider_response: dict[str, object],
        analysis: dict[str, object],
        derived_document_version_id: str,
    ) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE visual_analysis_jobs
                SET status = 'succeeded', provider_request = %s, provider_response = %s,
                    analysis = %s, derived_document_version_id = %s, error_code = NULL,
                    error_message = NULL, completed_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (
                    _as_json(provider_request), _as_json(provider_response), _as_json(analysis),
                    UUID(derived_document_version_id), UUID(job_id),
                ),
            )
            conn.commit()

    def record_visual_analysis_cancelled(
        self,
        job_id: str,
        *,
        provider_request: dict[str, object] | None = None,
        provider_response: dict[str, object] | None = None,
    ) -> None:
        with connection() as conn:
            conn.execute(
                """UPDATE visual_analysis_jobs
                   SET status = 'cancelled', provider_request = COALESCE(%s, provider_request),
                       provider_response = COALESCE(%s, provider_response),
                       error_code = 'CANCELLED_BY_USER',
                       error_message = '已取消；外部分析结果未写入知识库。',
                       completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                (_as_json(provider_request) if provider_request else None,
                 _as_json(provider_response) if provider_response else None, UUID(job_id)),
            )
            conn.commit()

    def record_visual_analysis_failure_or_retry(self, job_id: str, *, error_code: str, error_message: str) -> dict[str, object]:
        """Persist the failure reason and requeue exactly one retry when eligible."""
        with connection() as conn:
            row = conn.execute(
                """SELECT id, status, retry_count, cancel_requested_at
                   FROM visual_analysis_jobs WHERE id = %s FOR UPDATE""",
                (UUID(job_id),),
            ).fetchone()
            if not row:
                raise ValueError("Visual analysis job not found")
            if row["cancel_requested_at"] is not None:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'cancelled', error_code = 'CANCELLED_BY_USER',
                           error_message = '已取消；任务失败结果未写入知识库。',
                           completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                    (row["id"],),
                )
                outcome = "cancelled"
            elif int(row["retry_count"] or 0) < 1:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'queued', retry_count = retry_count + 1,
                           error_code = %s, error_message = %s,
                           materialization_started_at = NULL, updated_at = NOW()
                       WHERE id = %s""",
                    (error_code, error_message[:4000], row["id"]),
                )
                outcome = "retry_queued"
            else:
                conn.execute(
                    """UPDATE visual_analysis_jobs
                       SET status = 'failed', error_code = %s, error_message = %s,
                           completed_at = NOW(), updated_at = NOW() WHERE id = %s""",
                    (error_code, error_message[:4000], row["id"]),
                )
                outcome = "failed"
            conn.commit()
        return {"outcome": outcome, "job": self.get_visual_analysis_job(job_id) or {}}

    def _validate_visual_source(self, ingestion_job_id: str) -> dict[str, object]:
        source = self.get_ingestion_job(ingestion_job_id)
        if not source:
            raise ValueError("Ingestion job not found")
        if source["processing_route"] == "mineru_vlm_then_vision" and (
            source["status"] != "succeeded" or not source["document_version_id"]
        ):
            raise ValueError("OCR-plus-vision analysis requires a succeeded, materialized MinerU job")
        if str(source["file_type"]).lower() not in {"png", "jpg", "jpeg", "webp"}:
            raise ValueError("Visual analysis currently supports archived PNG, JPG, JPEG, and WEBP images")
        return source

    def record_normalized_image_assets(
        self,
        *,
        document_id: str,
        document_version_id: str,
        ingestion_job_id: str,
        image_assets: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Persist every structured PDF/PPTX image region and link it to its page/chunk.

        The method deliberately stores image metadata even for decorative records. Only
        ``decorative`` omits a child vector record, as decided before this point.
        """
        if not image_assets:
            return []
        with connection() as conn:
            parent_rows = conn.execute(
                """SELECT id, page_start FROM chunks
                   WHERE document_version_id = %s AND chunk_level = 'parent'""",
                (UUID(document_version_id),),
            ).fetchall()
            parent_by_page = {int(row["page_start"]): row["id"] for row in parent_rows if row["page_start"] is not None}
            child_rows = conn.execute(
                """SELECT id, metadata FROM chunks
                   WHERE document_version_id = %s AND chunk_level = 'child'""",
                (UUID(document_version_id),),
            ).fetchall()
            child_by_ref: dict[str, UUID] = {}
            for row in child_rows:
                metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
                ref = metadata.get("image_asset_ref")
                if isinstance(ref, str):
                    child_by_ref[ref] = row["id"]
            original = conn.execute(
                """SELECT id FROM document_assets
                   WHERE ingestion_job_id = %s AND asset_kind = 'original'
                   ORDER BY created_at ASC LIMIT 1""",
                (UUID(ingestion_job_id),),
            ).fetchone()
            original_asset_id = original["id"] if original else None
            stored: list[dict[str, object]] = []
            for raw in image_assets:
                asset_ref = str(raw["asset_ref"])
                page_number = raw.get("page_number")
                page = int(page_number) if page_number is not None else None
                document_asset_id = None
                storage_path = raw.get("storage_path")
                if isinstance(storage_path, str) and storage_path:
                    document_asset_id = uuid4()
                    conn.execute(
                        """INSERT INTO document_assets
                           (id, document_id, document_version_id, ingestion_job_id, source_asset_id,
                            asset_kind, file_name, storage_path, content_type, content_sha256,
                            size_bytes, metadata, derivation_type, page_start, page_end, generator_version)
                           VALUES (%s, %s, %s, %s, %s, 'image', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            document_asset_id, UUID(document_id), UUID(document_version_id), UUID(ingestion_job_id),
                            original_asset_id, str(raw.get("file_name") or f"image-{asset_ref[:12]}"), storage_path,
                            raw.get("content_type"), raw.get("image_hash"), raw.get("size_bytes"),
                            _as_json(raw.get("metadata", {})), "mineru_extract", page, page,
                            "mineru-structured-result",
                        ),
                    )
                    if original_asset_id:
                        conn.execute(
                            """INSERT INTO document_asset_derivations
                               (id, parent_asset_id, child_asset_id, derivation_type, page_start, page_end,
                                source_sha256, derivative_sha256, generator_version)
                               VALUES (%s, %s, %s, 'mineru_extract', %s, %s, %s, %s, 'mineru-structured-result')
                               ON CONFLICT (parent_asset_id, child_asset_id, derivation_type) DO NOTHING""",
                            (uuid4(), original_asset_id, document_asset_id, page, page, raw.get("source_sha256"), raw.get("image_hash")),
                        )
                image_asset_id = uuid4()
                child_chunk_id = child_by_ref.get(asset_ref)
                parent_chunk_id = parent_by_page.get(page) if page is not None else None
                conn.execute(
                    """INSERT INTO image_assets
                       (id, document_id, document_version_id, document_asset_id, parent_chunk_id, child_chunk_id,
                        page_number, bbox, image_hash, perceptual_hash, image_classification, processing_status,
                        source_storage_key, derivative_storage_key, adjacent_text, caption_text, table_context,
                        metadata, review_status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        image_asset_id, UUID(document_id), UUID(document_version_id), document_asset_id, parent_chunk_id,
                        child_chunk_id, page, _as_json(raw.get("bbox", {})), raw.get("image_hash"), raw.get("perceptual_hash"),
                        raw["image_classification"], raw["processing_status"], raw.get("source_storage_key"),
                        raw.get("derivative_storage_key"), str(raw.get("adjacent_text") or ""),
                        str(raw.get("caption_text") or ""), _as_json(raw.get("table_context", {})),
                        _as_json({**(raw.get("metadata", {}) if isinstance(raw.get("metadata"), dict) else {}), "asset_ref": asset_ref}),
                        str(raw.get("review_status") or "pending"),
                    ),
                )
                if child_chunk_id:
                    conn.execute(
                        """UPDATE chunks
                           SET metadata = jsonb_set(metadata, '{image_asset_id}', to_jsonb(%s::text), true),
                               updated_at = NOW()
                           WHERE id = %s""",
                        (str(image_asset_id), child_chunk_id),
                    )
                stored.append({"id": str(image_asset_id), "asset_ref": asset_ref, "page_number": page})
            conn.commit()
        return stored

    def ensure_visual_analysis_derivative_asset(
        self,
        *,
        ingestion_job_id: str,
        source_sha256: str,
        derivative_path: str,
        derivative_sha256: str,
        content_type: str,
        size_bytes: int,
        derivation_type: str,
        generator_version: str,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        """Persist a local vision derivative and its original-to-derived asset edge.

        The path remains PostgreSQL-only provenance and is intentionally never copied to
        Qdrant payloads or public Agent responses.
        """
        with connection() as conn:
            source = conn.execute(
                """SELECT document_id FROM ingestion_jobs WHERE id = %s FOR UPDATE""",
                (UUID(ingestion_job_id),),
            ).fetchone()
            if not source:
                raise ValueError("Ingestion job not found for visual derivative")
            original = conn.execute(
                """SELECT id FROM document_assets
                   WHERE ingestion_job_id = %s AND asset_kind = 'original' AND content_sha256 = %s
                   ORDER BY created_at ASC LIMIT 1""",
                (UUID(ingestion_job_id), source_sha256),
            ).fetchone()
            if not original:
                raise ValueError("Original document asset not found for visual derivative")
            existing = conn.execute(
                """SELECT id, storage_path, content_sha256 FROM document_assets
                   WHERE source_asset_id = %s AND derivation_type = %s AND content_sha256 = %s
                   ORDER BY created_at ASC LIMIT 1""",
                (original["id"], derivation_type, derivative_sha256),
            ).fetchone()
            if existing:
                conn.commit()
                return dict(existing)

            derivative_id = uuid4()
            derivative_file_name = Path(derivative_path).name
            conn.execute(
                """INSERT INTO document_assets
                   (id, document_id, ingestion_job_id, source_asset_id, asset_kind, file_name, storage_path,
                    content_type, content_sha256, size_bytes, metadata, derivation_type, generator_version)
                   VALUES (%s, %s, %s, %s, 'image', %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    derivative_id, source["document_id"], UUID(ingestion_job_id), original["id"],
                    derivative_file_name, derivative_path, content_type, derivative_sha256, size_bytes,
                    _as_json(metadata), derivation_type, generator_version,
                ),
            )
            conn.execute(
                """INSERT INTO document_asset_derivations
                   (id, parent_asset_id, child_asset_id, derivation_type, source_sha256, derivative_sha256,
                    generator_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (parent_asset_id, child_asset_id, derivation_type) DO NOTHING""",
                (
                    uuid4(), original["id"], derivative_id, derivation_type, source_sha256,
                    derivative_sha256, generator_version,
                ),
            )
            conn.commit()
        return {"id": str(derivative_id), "storage_path": derivative_path, "content_sha256": derivative_sha256}

    def find_document_asset_by_hash(self, *, document_id: str, content_sha256: str) -> dict[str, object] | None:
        """Return a local asset identity for derivation tooling; never use it in API responses."""
        with connection() as conn:
            row = conn.execute(
                """SELECT id, storage_path, file_name, content_sha256 FROM document_assets
                   WHERE document_id = %s AND content_sha256 = %s ORDER BY created_at ASC LIMIT 1""",
                (UUID(document_id), content_sha256),
            ).fetchone()
        return dict(row) if row else None
