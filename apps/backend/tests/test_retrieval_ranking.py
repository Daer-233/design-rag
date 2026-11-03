from types import SimpleNamespace

from app.providers.vector.base import SearchFilter
from app.providers.vector.qdrant_provider import QdrantVectorProvider
from app.services.coarse_ranker import rank_candidates


def test_rrf_fuses_dense_and_sparse_results() -> None:
    dense = [SimpleNamespace(id="a", score=0.9), SimpleNamespace(id="b", score=0.8)]
    sparse = [SimpleNamespace(id="b", score=5.0), SimpleNamespace(id="c", score=4.0)]

    candidates = QdrantVectorProvider._rrf_fuse(dense, sparse)

    assert [item.chunk_id for item in candidates] == ["b", "a", "c"]
    assert candidates[0].dense_rank == 2
    assert candidates[0].sparse_rank == 1


def test_child_level_is_a_hard_vector_filter() -> None:
    query_filter = QdrantVectorProvider._access_filter(
        SearchFilter(project_id="project-1", principal_roles=["designer"], chunk_level="child")
    )

    assert len(query_filter.must) == 2
    assert query_filter.must[1].key == "chunk_level"
    assert query_filter.must[1].match.value == "child"


def test_coarse_rank_returns_explainable_breakdown() -> None:
    items = [
        {
            "fusion_score": 0.03,
            "chunk_metadata": {"source_quality_score": 0.9, "topic": "materials"},
            "section_path": "材料 / 环保板材",
            "review_status": "approved",
            "source_confidence": 1.0,
        },
        {
            "fusion_score": 0.01,
            "chunk_metadata": {},
            "section_path": "照明",
            "review_status": "pending",
            "source_confidence": 0.5,
        },
    ]

    ranked = rank_candidates("环保板材选择", items, limit=10)

    assert len(ranked) == 2
    assert ranked[0].score > ranked[1].score
    assert set(ranked[0].breakdown) == {
        "fusion_normalized", "metadata_match", "review_status", "source_confidence", "source_quality", "evidence_type"
    }


def test_vector_payload_accepts_reloaded_postgres_chunk_mapping() -> None:
    from app.services.rag_service import IngestionService

    payload = IngestionService._vector_payload(
        {
            "chunk_id": "child-1", "chunk_level": "child", "parent_chunk_id": "parent-1",
            "project_id": "project-1", "document_id": "document-1",
            "chunk_access_scope": {"roles": ["designer"]}, "review_status": "approved",
            "source_confidence": 0.9, "file_type": "pdf", "chunk_type": "image_ocr",
            "metadata": {"content_origin": "ocr_extracted"}, "page_start": 2, "page_end": 2,
        }
    )

    assert payload["chunk_id"] == "child-1"
    assert payload["parent_chunk_id"] == "parent-1"
    assert payload["access_open"] is False
    assert payload["access_roles"] == ["designer"]
    assert payload["content_origin"] == "ocr_extracted"


def test_coarse_rank_prefers_fact_evidence_over_vision_when_recall_is_equal() -> None:
    items = [
        {"fusion_score": 0.02, "chunk_type": "vision_analysis", "chunk_metadata": {"content_origin": "vision_generated"}, "review_status": "approved", "source_confidence": 1.0},
        {"fusion_score": 0.02, "chunk_type": "material_item", "chunk_metadata": {"content_origin": "ocr_extracted"}, "review_status": "approved", "source_confidence": 1.0},
    ]
    ranked = rank_candidates("M-01 型号和规格", items, limit=2)
    assert ranked[0].item["chunk_type"] == "material_item"


def test_authority_filter_matches_safe_type_origin_and_page_fields() -> None:
    from app.services.rag_service import SearchService

    item = {"file_type": "pdf", "chunk_type": "image_ocr", "page_start": 3, "page_end": 3, "chunk_metadata": {"content_origin": "ocr_extracted"}}
    assert SearchService._matches_retrieval_filters(item, {"file_types": ["PDF"], "chunk_types": ["image_ocr"], "content_origins": ["ocr_extracted"], "page_start": 2, "page_end": 4})
    assert not SearchService._matches_retrieval_filters(item, {"page_start": 4})


def test_document_diversity_keeps_ranking_order_and_caps_each_document() -> None:
    from app.services.rag_service import SearchService

    ordered = [
        (0, SimpleNamespace(item={"document_id": "document-a"})),
        (1, SimpleNamespace(item={"document_id": "document-a"})),
        (2, SimpleNamespace(item={"document_id": "document-a"})),
        (3, SimpleNamespace(item={"document_id": "document-b"})),
        (4, SimpleNamespace(item={"document_id": "document-c"})),
    ]

    selected = SearchService._select_document_diverse_candidates(
        ordered, limit=4, max_per_document=2
    )

    assert [index for index, _candidate in selected] == [0, 1, 3, 4]



def test_document_diversity_builds_a_balanced_rerank_pool() -> None:
    from app.services.rag_service import SearchService

    # The top coarse candidates are dominated by A. A capped rerank pool still
    # retains the next documents, so the final top-k can be diverse and full.
    ordered = [
        (0, SimpleNamespace(item={"document_id": "document-a"})),
        (1, SimpleNamespace(item={"document_id": "document-a"})),
        (2, SimpleNamespace(item={"document_id": "document-a"})),
        (3, SimpleNamespace(item={"document_id": "document-b"})),
        (4, SimpleNamespace(item={"document_id": "document-c"})),
    ]

    selected = SearchService._select_document_diverse_candidates(
        ordered, limit=4, max_per_document=2
    )

    assert [index for index, _candidate in selected] == [0, 1, 3, 4]

def test_document_diversity_does_not_merge_legacy_rows_without_document_id() -> None:
    from app.services.rag_service import SearchService

    ordered = [
        (0, SimpleNamespace(item={})),
        (1, SimpleNamespace(item={})),
    ]

    selected = SearchService._select_document_diverse_candidates(
        ordered, limit=2, max_per_document=1
    )

    assert [index for index, _candidate in selected] == [0, 1]
