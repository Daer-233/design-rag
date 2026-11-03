-- Batch-import lifecycle plus first-class anonymous Agent retrieval audits.
-- PostgreSQL stays authoritative; raw sources/derivatives remain on local disk.

ALTER TABLE search_audit_logs
    DROP CONSTRAINT IF EXISTS search_audit_logs_identity_source_check;
ALTER TABLE search_audit_logs
    ADD CONSTRAINT search_audit_logs_identity_source_check
    CHECK (identity_source IN ('token_principal', 'requester_header', 'anonymous_agent'));
ALTER TABLE search_audit_logs
    ADD COLUMN IF NOT EXISTS agent_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES agent_conversations(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS retrieval_query_text TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_search_audit_agent_run
    ON search_audit_logs(agent_run_id) WHERE agent_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_search_audit_conversation_requested_at
    ON search_audit_logs(conversation_id, requested_at DESC) WHERE conversation_id IS NOT NULL;

ALTER TABLE ingestion_jobs
    DROP CONSTRAINT IF EXISTS ingestion_jobs_processing_route_check;
ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_processing_route_check
    CHECK (processing_route IN ('mineru_vlm', 'vision', 'mineru_vlm_then_vision', 'legacy_mineru_pipeline', 'local_preprocess'));

CREATE TABLE IF NOT EXISTS import_batches (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    project_external_key TEXT NOT NULL,
    project_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'uploading', 'preprocessing', 'awaiting_approval', 'queued', 'processing',
        'materializing', 'indexing', 'succeeded', 'partially_succeeded', 'failed'
    )),
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prepared_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_import_batches_project_updated ON import_batches(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_batches_status_updated ON import_batches(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS import_batch_items (
    id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    existing_ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    source_relative_path TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_file_type TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes BIGINT NOT NULL CHECK (source_size_bytes >= 0),
    source_file_path TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('archived', 'duplicate_skipped', 'failed')),
    error_code TEXT,
    error_message TEXT,
    processing_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(batch_id, source_relative_path)
);
CREATE INDEX IF NOT EXISTS idx_import_batch_items_batch ON import_batch_items(batch_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_import_batch_items_hash ON import_batch_items(source_sha256);

CREATE TABLE IF NOT EXISTS import_batch_tasks (
    id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES import_batch_items(id) ON DELETE CASCADE,
    task_kind TEXT NOT NULL CHECK (task_kind IN ('mineru', 'vision')),
    ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    visual_analysis_job_id UUID REFERENCES visual_analysis_jobs(id) ON DELETE SET NULL,
    source_ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes BIGINT NOT NULL CHECK (source_size_bytes >= 0),
    page_start INTEGER,
    page_end INTEGER,
    external_target TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_approval', 'queued', 'processing', 'succeeded', 'failed', 'skipped')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(batch_id, task_kind, ingestion_job_id)
);
CREATE INDEX IF NOT EXISTS idx_import_batch_tasks_batch ON import_batch_tasks(batch_id, created_at ASC);

