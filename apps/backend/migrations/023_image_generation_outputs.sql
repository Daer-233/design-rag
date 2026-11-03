CREATE TABLE IF NOT EXISTS image_generation_outputs (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES image_generation_jobs(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id),
    asset_type TEXT NOT NULL DEFAULT 'ai_concept_image',
    storage_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    width INTEGER,
    height INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_image_generation_outputs_job ON image_generation_outputs(job_id);
