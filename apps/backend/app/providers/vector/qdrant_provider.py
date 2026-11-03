"""Qdrant implementation. This is the only module that imports qdrant_client."""

from collections.abc import Iterable

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import settings
from app.providers.vector.base import SearchFilter, VectorCandidate, VectorRecord


class QdrantVectorProvider:
    provider_name = "qdrant"
    dense_vector_name = "dense"
    sparse_vector_name = "sparse"

    def __init__(self, *, url: str | None = None, collection_name: str | None = None) -> None:
        self.collection_name = collection_name or settings.qdrant_collection
        self.client = QdrantClient(url=url or settings.qdrant_url, timeout=30)

    def health(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False

    def ensure_collection(self, dense_dimension: int) -> None:
        created = False
        if not self.client.collection_exists(self.collection_name):
            created = True
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    self.dense_vector_name: models.VectorParams(
                        size=dense_dimension,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    self.sparse_vector_name: models.SparseVectorParams()
                },
                on_disk_payload=True,
            )
        # Existing Step-5 collections need a new index for child-only retrieval;
        # new collections get all filtering indexes at creation time.
        fields = (
            (
                ("project_id", models.PayloadSchemaType.KEYWORD),
                ("access_open", models.PayloadSchemaType.BOOL),
                ("access_roles", models.PayloadSchemaType.KEYWORD),
                ("document_id", models.PayloadSchemaType.KEYWORD),
                ("chunk_level", models.PayloadSchemaType.KEYWORD),
                ("parent_chunk_id", models.PayloadSchemaType.KEYWORD),
            )
            if created
            else (("chunk_level", models.PayloadSchemaType.KEYWORD),)
        )
        for field_name, schema in fields:
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=schema,
            )

    def upsert_chunks(self, records: list[VectorRecord], dense_dimension: int) -> None:
        if not records:
            return
        self.ensure_collection(dense_dimension)
        points = [
            models.PointStruct(
                id=record.point_id,
                vector={
                    self.dense_vector_name: record.dense_vector,
                    self.sparse_vector_name: models.SparseVector(
                        indices=record.sparse_indices,
                        values=record.sparse_values,
                    ),
                },
                payload=record.payload,
            )
            for record in records
        ]
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def hybrid_search(
        self,
        *,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        search_filter: SearchFilter,
        limit: int,
    ) -> list[VectorCandidate]:
        query_filter = self._access_filter(search_filter)
        dense = self.client.query_points(
            collection_name=self.collection_name,
            query=dense_vector,
            using=self.dense_vector_name,
            query_filter=query_filter,
            limit=limit,
            with_payload=False,
        ).points
        sparse = self.client.query_points(
            collection_name=self.collection_name,
            query=models.SparseVector(indices=sparse_indices, values=sparse_values),
            using=self.sparse_vector_name,
            query_filter=query_filter,
            limit=limit,
            with_payload=False,
        ).points
        return self._rrf_fuse(dense, sparse)

    @staticmethod
    def _rrf_fuse(
        dense: Iterable[object], sparse: Iterable[object], *, rrf_k: int = 60
    ) -> list[VectorCandidate]:
        by_chunk: dict[str, dict[str, float | int | None]] = {}
        for kind, points in (("dense", dense), ("sparse", sparse)):
            for rank, point in enumerate(points, start=1):
                chunk_id = str(point.id)
                current = by_chunk.setdefault(
                    chunk_id,
                    {
                        "dense_score": None,
                        "dense_rank": None,
                        "sparse_score": None,
                        "sparse_rank": None,
                        "fusion_score": 0.0,
                    },
                )
                current[f"{kind}_score"] = float(point.score)
                current[f"{kind}_rank"] = rank
                current["fusion_score"] = float(current["fusion_score"]) + 1.0 / (rrf_k + rank)
        candidates = [
            VectorCandidate(
                chunk_id=chunk_id,
                dense_score=item["dense_score"],  # type: ignore[arg-type]
                dense_rank=item["dense_rank"],  # type: ignore[arg-type]
                sparse_score=item["sparse_score"],  # type: ignore[arg-type]
                sparse_rank=item["sparse_rank"],  # type: ignore[arg-type]
                fusion_score=float(item["fusion_score"]),
            )
            for chunk_id, item in by_chunk.items()
        ]
        return sorted(candidates, key=lambda item: item.fusion_score, reverse=True)

    @staticmethod
    def _access_filter(search_filter: SearchFilter) -> models.Filter:
        access_conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="access_open", match=models.MatchValue(value=True))
        ]
        if search_filter.principal_roles:
            access_conditions.append(
                models.FieldCondition(
                    key="access_roles",
                    match=models.MatchAny(any=search_filter.principal_roles),
                )
            )
        must = [
            models.FieldCondition(
                key="chunk_level", match=models.MatchValue(value=search_filter.chunk_level)
            )
        ]
        if search_filter.project_id is not None:
            must.insert(
                0,
                models.FieldCondition(
                    key="project_id", match=models.MatchValue(value=search_filter.project_id)
                ),
            )
        if search_filter.file_types:
            must.append(models.FieldCondition(key="file_type", match=models.MatchAny(any=search_filter.file_types)))
        if search_filter.chunk_types:
            must.append(models.FieldCondition(key="chunk_type", match=models.MatchAny(any=search_filter.chunk_types)))
        if search_filter.content_origins:
            must.append(models.FieldCondition(key="content_origin", match=models.MatchAny(any=search_filter.content_origins)))
        if search_filter.page_start is not None:
            must.append(models.FieldCondition(key="page_end", range=models.Range(gte=search_filter.page_start)))
        if search_filter.page_end is not None:
            must.append(models.FieldCondition(key="page_start", range=models.Range(lte=search_filter.page_end)))
        return models.Filter(must=must, should=access_conditions)
