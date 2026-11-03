"""Rebuild non-indexed Qdrant records from PostgreSQL authority data.

This is a local-only recovery tool: it reads authoritative chunks from PostgreSQL,
creates vectors through the local BGE-M3 service, and writes the rebuildable Qdrant
index. It never uploads source files or calls MinerU/LLM providers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from app.clients.inference import EmbeddingClient
from app.db.connection import connection
from app.providers.vector.base import VectorRecord
from app.providers.vector.factory import get_vector_provider
from app.repositories.rag_repository import RagRepository
from app.services.rag_service import IngestionService


def pending_records(
    project_external_key: str | None,
    document_version_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, object]]:
    statement = """
        SELECT v.chunk_id, v.embedding_model_version_id
        FROM vector_index_records v
        JOIN chunks c ON c.id = v.chunk_id
        JOIN document_versions dv ON dv.id = c.document_version_id
        JOIN documents d ON d.id = dv.document_id
        JOIN projects p ON p.id = d.project_id
        WHERE v.index_status <> 'indexed'
    """
    params: list[object] = []
    if project_external_key:
        statement += " AND p.external_key = %s"
        params.append(project_external_key)
    if document_version_ids:
        statement += " AND dv.id = ANY(%s::uuid[])"
        params.append(document_version_ids)
    statement += " ORDER BY v.updated_at ASC"
    if limit:
        statement += " LIMIT %s"
        params.append(limit)
    with connection() as conn:
        return [dict(row) for row in conn.execute(statement, params).fetchall()]


def groups(items: list[dict[str, object]], size: int) -> list[list[dict[str, object]]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-external-key", default=None)
    parser.add_argument(
        "--document-version-id",
        action="append",
        dest="document_version_ids",
        help="Restrict rebuilding to a document version UUID. Repeat for multiple versions.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--write-batch-size",
        type=int,
        default=16,
        help="Number of chunks to persist to Qdrant after each local embedding cycle.",
    )
    args = parser.parse_args()
    if args.write_batch_size < 1:
        parser.error("--write-batch-size must be at least 1")

    pending = pending_records(args.project_external_key, args.document_version_ids, args.limit)
    if not pending:
        print("No non-indexed vector records found.")
        return 0

    repository = RagRepository()
    embedding = EmbeddingClient()
    provider = get_vector_provider()
    service = IngestionService(repository, embedding)
    succeeded = 0
    failed = 0
    print(f"Reindexing {len(pending)} authoritative chunks via {provider.provider_name}.", flush=True)

    for position, record_group in enumerate(groups(pending, args.write_batch_size), start=1):
        chunk_ids = [str(row["chunk_id"]) for row in record_group]
        try:
            chunk_map = repository.fetch_chunks(chunk_ids)
            ordered = [chunk_map[chunk_id] for chunk_id in chunk_ids if chunk_id in chunk_map]
            if len(ordered) != len(chunk_ids):
                missing = len(chunk_ids) - len(ordered)
                raise RuntimeError(f"{missing} vector record(s) could not be loaded from PostgreSQL")

            embedded = embedding.embed_many([str(chunk["content"]) for chunk in ordered])
            records = [
                VectorRecord(
                    point_id=str(chunk["chunk_id"]),
                    chunk_id=str(chunk["chunk_id"]),
                    dense_vector=embedded["dense_vectors"][index],
                    sparse_indices=embedded["sparse_vectors"][index]["indices"],
                    sparse_values=embedded["sparse_vectors"][index]["values"],
                    payload=service._vector_payload(chunk),
                )
                for index, chunk in enumerate(ordered)
            ]
            provider.upsert_chunks(records, int(embedded["dense_dimension"]))
            repository.mark_index_status([str(chunk["chunk_id"]) for chunk in ordered], status="indexed")
            for model_version_id in {str(row["embedding_model_version_id"]) for row in record_group}:
                repository.set_model_dense_dimension(model_version_id, int(embedded["dense_dimension"]))
            succeeded += len(records)
            print(
                f"Batch {position}: indexed {len(records)} chunks; "
                f"dense_dimension={embedded['dense_dimension']}; total_indexed={succeeded}.",
                flush=True,
            )
        except Exception as error:
            failed += len(chunk_ids)
            message = f"local vector reindex failed ({type(error).__name__}): {error}"
            repository.mark_index_status(chunk_ids, status="failed", error=message)
            print(f"Batch {position}: FAILED for {len(chunk_ids)} chunks: {message}", flush=True)

    print(
        f"Reindex complete: indexed={succeeded}; failed={failed}; provider={provider.provider_name}.",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
