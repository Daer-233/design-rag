-- Durable, explicitly-submitted image-analysis jobs.
-- PostgreSQL stores the source hash, model/prompt version, normalized result and
-- derived document version. The image bytes are never copied into job JSON.
CREATE TABLE IF NOT EXISTS visual_analysis_jobs (
    id UUID PRIMARY KEY,
    ingestion_job_id UUID NOT NULL REFERENCES ingestion_jobs(id),
    source_document_version_id UUID NOT NULL REFERENCES document_versions(id),
    derived_document_version_id UUID REFERENCES document_versions(id),
    model_version_id UUID NOT NULL REFERENCES model_versions(id),
    provider_name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created', 'succeeded', 'failed', 'cancelled')),
    source_sha256 TEXT NOT NULL,
    provider_request JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visual_analysis_jobs_ingestion_job_id
    ON visual_analysis_jobs(ingestion_job_id);
CREATE INDEX IF NOT EXISTS idx_visual_analysis_jobs_status
    ON visual_analysis_jobs(status);
CREATE INDEX IF NOT EXISTS idx_visual_analysis_jobs_source_document_version_id
    ON visual_analysis_jobs(source_document_version_id);
