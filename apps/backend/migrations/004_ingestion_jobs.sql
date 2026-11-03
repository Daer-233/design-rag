-- Durable external-parser task records. Source files and MinerU output are archived outside PostgreSQL;
-- PostgreSQL retains their hash, path, provenance and lifecycle as the system of record.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    document_version_id UUID REFERENCES document_versions(id),
    provider_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'created', 'submitted', 'processing', 'succeeded', 'failed', 'cancelled'
    )),
    source_sha256 TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    source_content_type TEXT,
    source_size_bytes BIGINT NOT NULL CHECK (source_size_bytes >= 0),
    provider_task_id TEXT,
    provider_batch_id TEXT,
    provider_request JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_path TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_code TEXT,
    error_message TEXT,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_assets (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    document_version_id UUID REFERENCES document_versions(id),
    ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    asset_kind TEXT NOT NULL CHECK (asset_kind IN (
        'original', 'parser_result', 'markdown', 'layout_json', 'image', 'table', 'other'
    )),
    file_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    content_type TEXT,
    content_sha256 TEXT,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document_id ON ingestion_jobs(document_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_provider_task_id ON ingestion_jobs(provider_name, provider_task_id);
CREATE INDEX IF NOT EXISTS idx_document_assets_document_id ON document_assets(document_id);
CREATE INDEX IF NOT EXISTS idx_document_assets_job_id ON document_assets(ingestion_job_id);
