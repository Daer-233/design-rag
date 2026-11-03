"""Read-only knowledge catalog queries shared by the API and MCP gateway."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.connection import connection
from app.services.access_policy import AccessPolicy, Principal


class KnowledgeCatalogRepository:
    """Expose project/document metadata without source paths or document bytes."""

    def __init__(self, access_policy: AccessPolicy | None = None) -> None:
        self.access_policy = access_policy or AccessPolicy()

    def list_projects(self, principal: Principal) -> list[dict[str, Any]]:
        where = ""
        params: tuple[object, ...] = ()
        if not principal.all_projects:
            if not principal.project_ids:
                return []
            project_ids = [UUID(value) for value in sorted(principal.project_ids)]
            where = "AND p.id = ANY(%s)"
            params = (project_ids,)
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.name, p.external_key,
                       COUNT(DISTINCT d.id) AS document_count,
                       MAX(d.updated_at) AS last_document_updated_at
                FROM projects p
                JOIN documents d ON d.project_id = p.id AND d.status = 'active'
                WHERE TRUE {where}
                GROUP BY p.id, p.name, p.external_key
                ORDER BY p.name ASC, p.id ASC
                """,
                params,
            ).fetchall()
        return [self._project(row) for row in rows]

    def list_project_documents(self, principal: Principal, project_id: str) -> list[dict[str, Any]]:
        self.access_policy.require_project_access(principal, project_id)
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.project_id, d.external_key, d.file_name, d.file_type, d.status,
                       d.metadata, d.created_at, d.updated_at,
                       latest.version_no, latest.parser_provider, latest.parser_model,
                       latest.parser_version, latest.source_confidence, latest.created_at AS version_created_at,
                       COALESCE(chunk_stats.chunk_count, 0) AS chunk_count,
                       COALESCE(chunk_stats.chunk_types, ARRAY[]::text[]) AS chunk_types,
                       COALESCE(chunk_stats.content_origins, ARRAY[]::text[]) AS content_origins
                FROM documents d
                LEFT JOIN LATERAL (
                    SELECT dv.version_no, dv.parser_provider, dv.parser_model, dv.parser_version,
                           dv.source_confidence, dv.created_at
                    FROM document_versions dv
                    WHERE dv.document_id = d.id
                    ORDER BY dv.version_no DESC
                    LIMIT 1
                ) latest ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*)::int AS chunk_count,
                           ARRAY_AGG(DISTINCT c.chunk_type ORDER BY c.chunk_type) AS chunk_types,
                           ARRAY_AGG(DISTINCT COALESCE(c.metadata->>'content_origin', 'document_text')
                                     ORDER BY COALESCE(c.metadata->>'content_origin', 'document_text')) AS content_origins
                    FROM chunks c
                    JOIN document_versions dv ON dv.id = c.document_version_id
                    WHERE dv.document_id = d.id AND c.parent_chunk_id IS NOT NULL
                ) chunk_stats ON TRUE
                WHERE d.project_id = %s AND d.status = 'active'
                ORDER BY d.file_name ASC, d.id ASC
                """,
                (UUID(project_id),),
            ).fetchall()
        return [self._document(row) for row in rows]

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "read_only": True,
            "project_scope": "token_derived",
            "tools": [
                "list_projects",
                "resolve_project",
                "list_project_documents",
                "search_knowledge_base",
                "search_materials",
                "get_retrieval_capabilities",
            ],
            "supported_filters": [
                "file_types",
                "chunk_types",
                "content_origins",
                "page_start",
                "page_end",
            ],
            "material_chunk_types": [
                "material_item",
                "material_swatch",
                "vision_surface",
                "furniture_material",
                "vision_analysis",
                "table",
            ],
            "content_origins": ["document_text", "ocr_extracted", "vision_generated"],
            "evidence_types": [
                "document_confirmed",
                "vision_inferred",
                "candidate_match",
                "unknown",
            ],
            "citation_fields": [
                "source_id",
                "project_id",
                "project_name",
                "document_id",
                "file_name",
                "file_type",
                "page_start",
                "page_end",
                "section_path",
            ],
        }

    @staticmethod
    def _project(row: object) -> dict[str, Any]:
        value = dict(row)  # type: ignore[arg-type]
        return {
            "id": str(value["id"]),
            "name": str(value["name"]),
            "external_key": str(value["external_key"]),
            "document_count": int(value.get("document_count") or 0),
            "last_document_updated_at": value.get("last_document_updated_at"),
        }

    @staticmethod
    def _document(row: object) -> dict[str, Any]:
        value = dict(row)  # type: ignore[arg-type]
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        safe_metadata = {
            key: metadata[key]
            for key in (
                "source",
                "source_relative_path",
                "source_kind",
                "source_document_name",
                "source_page_number",
                "derivation_type",
            )
            if key in metadata
        }
        return {
            "id": str(value["id"]),
            "project_id": str(value["project_id"]),
            "external_key": str(value["external_key"]),
            "file_name": str(value["file_name"]),
            "file_type": str(value["file_type"]),
            "status": str(value["status"]),
            "metadata": safe_metadata,
            "version": (
                {
                    "version_no": int(value["version_no"]),
                    "parser_provider": value.get("parser_provider"),
                    "parser_model": value.get("parser_model"),
                    "parser_version": value.get("parser_version"),
                    "source_confidence": value.get("source_confidence"),
                    "created_at": value.get("version_created_at"),
                }
                if value.get("version_no") is not None
                else None
            ),
            "chunk_count": int(value.get("chunk_count") or 0),
            "chunk_types": list(value.get("chunk_types") or []),
            "content_origins": list(value.get("content_origins") or []),
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
        }
