"""RAG orchestration: parent-child indexing, pre-filtered retrieval, coarse rank and rerank."""

from time import perf_counter
from typing import Any

from app.clients.inference import EmbeddingClient, InferenceServiceError, RerankClient
from app.core.config import settings
from app.providers.vector.base import SearchFilter, VectorRecord
from app.providers.vector.factory import get_vector_provider
from app.repositories.rag_repository import RagRepository
from app.services.access_policy import AccessDeniedError, AccessPolicy
from app.services.coarse_ranker import rank_candidates


class IngestionService:
    def __init__(self, repository: RagRepository, embedding: EmbeddingClient) -> None:
        self.repository = repository
        self.embedding = embedding
        self.provider = get_vector_provider()

    def ingest(self, payload: dict[str, object]) -> dict[str, Any]:
        identity = self.embedding.model_identity()
        authority = self.repository.store_document(
            payload=payload,
            embedding_model=identity,
            provider_name=self.provider.provider_name,
            collection_name=getattr(self.provider, "collection_name", settings.qdrant_collection),
        )
        try:
            embedding = self.embedding.embed_many([chunk.content for chunk in authority.chunks])
            records = [
                VectorRecord(
                    point_id=chunk.chunk_id,
                    chunk_id=chunk.chunk_id,
                    dense_vector=embedding["dense_vectors"][index],
                    sparse_indices=embedding["sparse_vectors"][index]["indices"],
                    sparse_values=embedding["sparse_vectors"][index]["values"],
                    payload=self._vector_payload(chunk),
                )
                for index, chunk in enumerate(authority.chunks)
            ]
            self.provider.upsert_chunks(records, int(embedding["dense_dimension"]))
            self.repository.set_model_dense_dimension(authority.model_version_id, int(embedding["dense_dimension"]))
            self.repository.mark_index_status([chunk.chunk_id for chunk in authority.chunks], status="indexed")
            index_status = "indexed"
            warning = None
        except Exception as error:
            warning = f"authoritative data stored; vector indexing pending ({type(error).__name__})"
            self.repository.mark_index_status(
                [chunk.chunk_id for chunk in authority.chunks], status="failed", error=warning
            )
            index_status = "failed"
        parent_chunk_ids = [chunk.chunk_id for chunk in authority.chunks if chunk.chunk_level == "parent"]
        child_chunk_ids = [chunk.chunk_id for chunk in authority.chunks if chunk.chunk_level == "child"]
        return {
            "project_id": authority.project_id,
            "document_id": authority.document_id,
            "document_version_id": authority.document_version_id,
            "embedding_model_version_id": authority.model_version_id,
            "parent_chunk_ids": parent_chunk_ids,
            "child_chunk_ids": child_chunk_ids,
            # Retained while clients migrate to the explicit parent/child fields.
            "chunk_ids": [chunk.chunk_id for chunk in authority.chunks],
            "index_status": index_status,
            "warning": warning,
        }

    @staticmethod
    def _vector_payload(chunk: object) -> dict[str, object]:
        """Build minimal Qdrant payload from a newly stored or reloaded authority chunk."""
        if isinstance(chunk, dict):
            def value(key: str) -> object:
                if key == "access_scope":
                    # fetch_chunks uses an explicit alias to distinguish the child
                    # access scope from the parent context returned in the same row.
                    return chunk.get("access_scope", chunk.get("chunk_access_scope", {}))
                return chunk[key]
        else:
            value = lambda key: getattr(chunk, key)
        scope = value("access_scope")
        roles = scope.get("roles", []) if isinstance(scope, dict) else []
        roles = [str(role) for role in roles] if isinstance(roles, list) else []
        return {
            "chunk_id": str(value("chunk_id")),
            "chunk_level": str(value("chunk_level")),
            "parent_chunk_id": (
                str(value("parent_chunk_id")) if value("parent_chunk_id") is not None else None
            ),
            "project_id": str(value("project_id")),
            "document_id": str(value("document_id")),
            "access_open": not roles,
            "access_roles": roles,
            "review_status": str(value("review_status")),
            "source_confidence": float(value("source_confidence")),
            "file_type": str(value("file_type")),
            "chunk_type": str(value("chunk_type")),
            "content_origin": str(value("metadata").get("content_origin", "document_text")),
            "page_start": value("page_start"),
            "page_end": value("page_end"),
        }


class SearchService:
    def __init__(
        self,
        repository: RagRepository,
        embedding: EmbeddingClient,
        reranker: RerankClient,
        access_policy: AccessPolicy,
    ) -> None:
        self.repository = repository
        self.embedding = embedding
        self.reranker = reranker
        self.access_policy = access_policy
        self.provider = get_vector_provider()

    def search(
        self,
        *,
        query: str,
        project_id: str | None,
        principal_raw: object,
        evidence_top_k: int | None,
        retrieval_filters: dict[str, Any] | None = None,
        redact_source_details: bool = False,
        include_images: bool = True,
        image_mode: str = "metadata",
        public_base_url: str | None = None,
        asset_url_prefix: str = '/api/v1/chat/assets',
    ) -> dict[str, Any]:
        started = perf_counter()
        authorization_started = perf_counter()
        # Hard authorization occurs before any vector request.
        principal = self.access_policy.to_principal(principal_raw)
        if project_id is not None:
            self.access_policy.require_project_access(principal, project_id)
            project_exists = self.repository.project_exists(project_id)
        else:
            if not principal.all_projects:
                raise AccessDeniedError("A project identifier is required for this principal")
            project_exists = True
        authorization_ms = (perf_counter() - authorization_started) * 1000
        if not project_exists:
            return self._empty_response(query, project_id, "project_not_found")

        embedding_started = perf_counter()
        embedding = self.embedding.embed([query])
        embedding_ms = (perf_counter() - embedding_started) * 1000

        vector_search_started = perf_counter()
        # Both parent and child points exist in Qdrant. Querying is intentionally limited
        # to children for precision; PostgreSQL then returns the parent as context.
        vectors = self.provider.hybrid_search(
            dense_vector=embedding["dense_vectors"][0],
            sparse_indices=embedding["sparse_vectors"][0]["indices"],
            sparse_values=embedding["sparse_vectors"][0]["values"],
            search_filter=SearchFilter(
                project_id=project_id,
                principal_roles=self.access_policy.roles_for_filter(principal),
                chunk_level="child",
                file_types=self._normalise_filter_values(retrieval_filters, "file_types"),
                chunk_types=self._normalise_filter_values(retrieval_filters, "chunk_types"),
                content_origins=self._normalise_filter_values(retrieval_filters, "content_origins"),
                page_start=self._filter_page(retrieval_filters, "page_start"),
                page_end=self._filter_page(retrieval_filters, "page_end"),
            ),
            limit=settings.retrieval_recall_top_k,
        )
        vector_search_ms = (perf_counter() - vector_search_started) * 1000

        authority_started = perf_counter()
        authoritative = self.repository.fetch_chunks([candidate.chunk_id for candidate in vectors])
        candidates: list[dict[str, Any]] = []
        for vector in vectors:
            row = authoritative.get(vector.chunk_id)
            # PostgreSQL is the authority and performs a second access check against stored scopes.
            if (
                not row
                or (project_id is not None and str(row["project_id"]) != project_id)
                or row["chunk_level"] != "child"
            ):
                continue
            if not self.access_policy.can_read_scope(row["chunk_access_scope"], principal):
                continue
            if not self._matches_retrieval_filters(row, retrieval_filters):
                continue
            # Children inherit their parent's scope on ingest. Keep this defensive parent
            # check so a corrupted hierarchy cannot leak expanded parent context.
            if row["parent_access_scope"] and not self.access_policy.can_read_scope(
                row["parent_access_scope"], principal
            ):
                continue
            candidates.append(
                {
                    **row,
                    "chunk_id": vector.chunk_id,
                    "dense_score": vector.dense_score,
                    "dense_rank": vector.dense_rank,
                    "sparse_score": vector.sparse_score,
                    "sparse_rank": vector.sparse_rank,
                    "fusion_score": vector.fusion_score,
                }
            )
        authority_ms = (perf_counter() - authority_started) * 1000

        coarse_started = perf_counter()
        coarse = rank_candidates(query, candidates, settings.retrieval_coarse_top_k)
        coarse_ms = (perf_counter() - coarse_started) * 1000
        rerank_candidate_limit = settings.effective_rerank_candidate_top_k
        rerank_candidates = self._select_document_diverse_candidates(
            list(enumerate(coarse)),
            limit=rerank_candidate_limit,
            max_per_document=settings.retrieval_max_evidence_per_document,
        )
        rerank_input = [candidate for _coarse_index, candidate in rerank_candidates]
        if not rerank_input:
            return self._empty_response(query, project_id, "no_accessible_evidence")

        rerank_started = perf_counter()
        rerank_results = self.reranker.rerank(query, [item.item["content"] for item in rerank_input])
        rerank_ms = (perf_counter() - rerank_started) * 1000
        rerank_score_by_index = {int(item["index"]): float(item["score"]) for item in rerank_results}
        ordered = sorted(
            enumerate(rerank_input),
            key=lambda pair: rerank_score_by_index.get(pair[0], float("-inf")),
            reverse=True,
        )
        evidence_limit = evidence_top_k or settings.retrieval_evidence_top_k
        selected_candidates = self._select_document_diverse_candidates(
            ordered,
            limit=evidence_limit,
            max_per_document=settings.retrieval_max_evidence_per_document,
        )
        assembly_started = perf_counter()
        evidence = [
            self._to_evidence(
                index,
                coarse_candidate,
                rerank_score_by_index.get(index),
                redact_source_details=redact_source_details,
            )
            for index, coarse_candidate in selected_candidates
        ]
        retrieval_images: list[dict[str, Any]] = []
        if include_images and evidence:
            retrieval_images = self._attach_images(
                evidence,
                project_id=project_id,
                image_mode=image_mode,
                public_base_url=public_base_url,
                asset_url_prefix=asset_url_prefix,
            )
        assembly_ms = (perf_counter() - assembly_started) * 1000
        return {
            "query": {"raw": query, "effective": query, "rewritten": False},
            "project_id": project_id,
            "pipeline": {
                "retrieval_unit": "child",
                "context_unit": "parent",
                "parent_points_indexed": True,
                "recall_top_k": settings.retrieval_recall_top_k,
                "coarse_top_k": settings.retrieval_coarse_top_k,
                # Preserve the response key while making its input-budget meaning explicit.
                "rerank_top_k": rerank_candidate_limit,
                "rerank_candidate_top_k": rerank_candidate_limit,
                "evidence_top_k": evidence_limit,
                "recall_count": len(vectors),
                "authoritative_candidate_count": len(candidates),
                "coarse_count": len(coarse),
                "rerank_input_count": len(rerank_input),
                "evidence_count": len(evidence),
                "evidence_shortfall": max(0, evidence_limit - len(evidence)),
                "performance_ms": {
                    "authorization": round(authorization_ms, 2),
                    "query_embedding": round(embedding_ms, 2),
                    "hybrid_vector_search": round(vector_search_ms, 2),
                    "authority_hydration_and_access_filter": round(authority_ms, 2),
                    "coarse_ranking": round(coarse_ms, 2),
                    "reranking": round(rerank_ms, 2),
                    "evidence_assembly": round(assembly_ms, 2),
                    "total": round((perf_counter() - started) * 1000, 2),
                },
            },
            "results": evidence,
            "images": retrieval_images,
        }

    @staticmethod
    def _select_document_diverse_candidates(
        ordered: list[tuple[int, object]],
        *,
        limit: int,
        max_per_document: int,
    ) -> list[tuple[int, object]]:
        """Keep ranking order while preventing one document from monopolising evidence."""
        selected: list[tuple[int, object]] = []
        document_counts: dict[str, int] = {}
        capped_limit = max(1, int(limit))
        document_limit = max(1, int(max_per_document))
        for index, candidate in ordered:
            item = getattr(candidate, "item", {})
            document_id = str(item.get("document_id") or "") if isinstance(item, dict) else ""
            # Authority rows always have a document id. Retain unknown legacy rows rather
            # than combining unrelated records under a synthetic shared document id.
            document_key = document_id or f"__unknown__:{index}"
            if document_counts.get(document_key, 0) >= document_limit:
                continue
            selected.append((index, candidate))
            document_counts[document_key] = document_counts.get(document_key, 0) + 1
            if len(selected) >= capped_limit:
                break
        return selected

    def _attach_images(
        self,
        evidence: list[dict[str, Any]],
        *,
        project_id: str | None,
        image_mode: str,
        public_base_url: str | None,
        asset_url_prefix: str,
    ) -> list[dict[str, Any]]:
        """Attach bounded, de-duplicated image references without failing text search."""
        lister = getattr(self.repository, "list_retrieval_image_assets", None)
        if not callable(lister):
            return []
        chunk_ids = [str(item.get("chunk_id")) for item in evidence if item.get("chunk_id")]
        try:
            rows = lister(chunk_ids, project_id=project_id, limit=100)
        except Exception:
            # Image enrichment is best-effort; a missing derivative must not break RAG.
            return []
        by_chunk: dict[str, list[dict[str, Any]]] = {str(item.get("chunk_id")): [] for item in evidence}
        top: list[dict[str, Any]] = []
        per_document: dict[str, int] = {}
        seen: set[str] = set()
        for row in rows:
            asset_id = str(row.get("id") or "")
            if not asset_id or asset_id in seen:
                continue
            if project_id and str(row.get("project_id")) != str(project_id):
                continue
            document_id = str(row.get("document_id") or "")
            if per_document.get(document_id, 0) >= 4 or len(top) >= 8:
                continue
            relation_rank = int(row.get("relation_rank") or 2)
            asset = self._image_reference(row, image_mode=image_mode, public_base_url=public_base_url, asset_url_prefix=asset_url_prefix)
            asset["relation"] = "chunk" if relation_rank == 0 else "page" if relation_rank == 1 else "document_page"
            top.append(asset)
            per_document[document_id] = per_document.get(document_id, 0) + 1
            seen.add(asset_id)
            for item in evidence:
                citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
                same_chunk = (
                    str(item.get("chunk_id")) == str(row.get("child_chunk_id"))
                    or str(item.get("chunk_id")) == str(row.get("parent_chunk_id"))
                )
                # Standalone JPG/PNG files have no PDF page and no extracted
                # child-image chunk. Their document identity is still authoritative.
                same_document = str(citation.get("document_id") or "") == document_id
                if same_chunk or same_document:
                    if len(by_chunk.setdefault(str(item.get("chunk_id")), [])) < 2:
                        by_chunk[str(item.get("chunk_id"))].append(asset)
        for item in evidence:
            item["images"] = by_chunk.get(str(item.get("chunk_id")), [])
        return top

    @staticmethod
    def _image_reference(row: dict[str, Any], *, image_mode: str, public_base_url: str | None, asset_url_prefix: str = "/api/v1/chat/assets") -> dict[str, Any]:
        asset_id = str(row["id"])
        base = (public_base_url or "").rstrip("/")
        from app.services.image_asset_access import sign_asset_access
        access_token = sign_asset_access(asset_id, str(row.get("project_id")) if row.get("project_id") else None)
        project_query = str(row.get("project_id") or "")
        access_query = (f"?project_id={project_query}&access_token={access_token}" if asset_url_prefix.startswith("/api/v1/mcp/") else f"?access_token={access_token}")
        route_prefix = str(asset_url_prefix or "/api/v1/chat/assets").rstrip("/")
        prefix = f"{base}{route_prefix}/{asset_id}" if base else f"{route_prefix}/{asset_id}"
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        asset_type = metadata.get("asset_type") or row.get("image_classification") or "unknown"
        result = {
            "asset_id": asset_id,
            "asset_type": str(asset_type),
            "image_classification": row.get("image_classification"),
            "document_id": str(row.get("document_id")) if row.get("document_id") else None,
            "document_name": row.get("file_name"),
            "file_name": row.get("file_name") or row.get("asset_file_name"),
            "page_number": row.get("page_number"),
            "bbox": row.get("bbox") or {},
            "relation": "citation",
            "thumbnail_url": f"{prefix}/thumbnail{access_query}",
            "preview_url": f"{prefix}/preview{access_query}",
            "original_url": f"{prefix}/original{access_query}",
            "resource_uri": f"design-rag://assets/{asset_id}",
            "evidence_type": "document_visual_evidence" if str(asset_type) not in {"rendering", "vision_source"} else "visual_observation",
            "vision_status": row.get("vision_status"),
        }
        if image_mode == "full":
            result["read_url"] = result["original_url"]
        return result

    @staticmethod
    def _normalise_filter_values(filters: dict[str, Any] | None, key: str) -> list[str] | None:
        values = filters.get(key) if isinstance(filters, dict) else None
        if not isinstance(values, list):
            return None
        normalized = sorted({str(value).strip().lower() for value in values if str(value).strip()})
        return normalized or None

    @staticmethod
    def _filter_page(filters: dict[str, Any] | None, key: str) -> int | None:
        value = filters.get(key) if isinstance(filters, dict) else None
        return int(value) if isinstance(value, int) and value > 0 else None

    @classmethod
    def _matches_retrieval_filters(cls, item: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        if not filters:
            return True
        def matches(value: object, key: str) -> bool:
            allowed = cls._normalise_filter_values(filters, key)
            return allowed is None or str(value).lower() in allowed
        metadata = item.get("chunk_metadata")
        origin = metadata.get("content_origin", "document_text") if isinstance(metadata, dict) else "document_text"
        if not matches(item.get("file_type"), "file_types"):
            return False
        if not matches(item.get("chunk_type"), "chunk_types"):
            return False
        if not matches(origin, "content_origins"):
            return False
        requested_start = cls._filter_page(filters, "page_start")
        requested_end = cls._filter_page(filters, "page_end")
        page_start = item.get("page_start")
        page_end = item.get("page_end")
        if requested_start is not None and isinstance(page_end, int) and page_end < requested_start:
            return False
        if requested_end is not None and isinstance(page_start, int) and page_start > requested_end:
            return False
        return True

    @staticmethod
    def _to_evidence(
        index: int,
        candidate: object,
        rerank_score: float | None,
        *,
        redact_source_details: bool = False,
    ) -> dict[str, Any]:
        item = candidate.item
        parent_context = None
        if item["parent_id"]:
            parent_context = {
                "chunk_id": str(item["parent_id"]),
                "content": item["parent_content"],
                "review_status": item["parent_review_status"],
                "source_confidence": item["parent_source_confidence"],
                "page_start": item["parent_page_start"],
                "page_end": item["parent_page_end"],
                "section_path": item["parent_section_path"],
            }
            if not redact_source_details:
                parent_context["metadata"] = item["parent_metadata"]
                parent_context["source_locator"] = item["parent_source_locator"]
        return {
            "chunk_id": item["chunk_id"],
            "chunk_level": item["chunk_level"],
            "content": item["content"],
            "parent_context": parent_context,
            "scores": {
                "dense": item["dense_score"],
                "dense_rank": item["dense_rank"],
                "sparse": item["sparse_score"],
                "sparse_rank": item["sparse_rank"],
                "fusion": item["fusion_score"],
                "coarse": candidate.score,
                "coarse_breakdown": candidate.breakdown,
                "rerank": rerank_score,
            },
            "citation": SearchService._citation(item, redact_source_details=redact_source_details),
        }

    @staticmethod
    def _citation(item: dict[str, Any], *, redact_source_details: bool) -> dict[str, Any]:
        """Project-stable citations without exposing host paths or raw locators."""
        citation: dict[str, Any] = {
            "source_id": f"document:{item['document_id']}",
            "project_id": str(item["project_id"]),
            "project_name": item["project_name"],
            "project_external_key": item["project_external_key"],
            "document_id": str(item["document_id"]),
            "file_name": item["file_name"],
            "file_type": item["file_type"],
            "page_start": item["page_start"],
            "page_end": item["page_end"],
            "section_path": item["section_path"],
        }
        if not redact_source_details:
            citation["source_uri"] = item["source_uri"]
            citation["source_locator"] = item["source_locator"]
        return citation

    @staticmethod
    def _empty_response(query: str, project_id: str | None, reason: str) -> dict[str, Any]:
        return {
            "query": {"raw": query, "effective": query, "rewritten": False},
            "project_id": project_id,
            "pipeline": {"reason": reason, "retrieval_unit": "child", "context_unit": "parent"},
            "results": [],
            "images": [],
        }


__all__ = ["AccessDeniedError", "InferenceServiceError", "IngestionService", "SearchService"]
