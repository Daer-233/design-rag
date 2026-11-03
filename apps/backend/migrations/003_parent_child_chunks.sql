-- Parent-child chunks: PostgreSQL remains authoritative; both levels are indexable in Qdrant.
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS chunk_level TEXT,
    ADD COLUMN IF NOT EXISTS parent_chunk_id UUID REFERENCES chunks(id);

-- Existing one-level records become parent records. Re-ingest legacy data in the new
-- parent-child payload format when it should participate in child-only retrieval.
UPDATE chunks
SET chunk_level = 'parent'
WHERE chunk_level IS NULL;

ALTER TABLE chunks
    ALTER COLUMN chunk_level SET DEFAULT 'parent',
    ALTER COLUMN chunk_level SET NOT NULL;

ALTER TABLE chunks
    DROP CONSTRAINT IF EXISTS chunks_document_version_id_chunk_ordinal_key;

ALTER TABLE chunks
    DROP CONSTRAINT IF EXISTS chunks_chunk_hierarchy_check;

ALTER TABLE chunks
    ADD CONSTRAINT chunks_chunk_hierarchy_check CHECK (
        (chunk_level = 'parent' AND parent_chunk_id IS NULL)
        OR
        (chunk_level = 'child' AND parent_chunk_id IS NOT NULL)
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_parent_ordinal
    ON chunks(document_version_id, chunk_ordinal)
    WHERE chunk_level = 'parent';

CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_child_ordinal
    ON chunks(parent_chunk_id, chunk_ordinal)
    WHERE chunk_level = 'child';

CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk_id ON chunks(parent_chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document_version_level ON chunks(document_version_id, chunk_level);
