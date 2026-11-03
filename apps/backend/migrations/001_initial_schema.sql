CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY,
    external_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id),
    external_key TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    content_sha256 TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, external_key)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    version_no INTEGER NOT NULL,
    parser_provider TEXT NOT NULL,
    parser_model TEXT NOT NULL,
    parser_version TEXT,
    parsed_content_uri TEXT,
    parsed_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, version_no)
);

CREATE TABLE IF NOT EXISTS model_versions (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('embedding', 'rerank', 'llm', 'parser')),
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    source_revision TEXT NOT NULL DEFAULT '',
    artifact_sha256 TEXT NOT NULL DEFAULT '',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    dense_dimension INTEGER CHECK (dense_dimension > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(kind, provider, model_id, source_revision, artifact_sha256)
);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL REFERENCES document_versions(id),
    chunk_ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    token_count INTEGER,
    chunk_type TEXT NOT NULL DEFAULT 'text',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    page_start INTEGER,
    page_end INTEGER,
    section_path TEXT,
    source_locator JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL DEFAULT 'pending',
    source_confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    embedding_model_version_id UUID REFERENCES model_versions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(document_version_id, chunk_ordinal)
);

CREATE TABLE IF NOT EXISTS vector_index_records (
    id UUID PRIMARY KEY,
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    provider_name TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    point_id TEXT NOT NULL,
    embedding_model_version_id UUID REFERENCES model_versions(id),
    index_status TEXT NOT NULL CHECK (index_status IN ('pending', 'indexed', 'failed', 'deleted')),
    indexed_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(provider_name, collection_name, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_project_id ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_document_versions_document_id ON document_versions(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document_version_id ON chunks(document_version_id);
CREATE INDEX IF NOT EXISTS idx_vector_index_records_status ON vector_index_records(index_status);

