-- Local background queues for explicitly approved external parser and vision work.
-- Queued jobs are held locally until the full local backend worker claims them.

ALTER TABLE ingestion_jobs
    DROP CONSTRAINT IF EXISTS ingestion_jobs_status_check;
ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_status_check
    CHECK (status IN ('created', 'queued', 'processing', 'submitted', 'succeeded', 'failed', 'cancelled'));
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_queued
    ON ingestion_jobs (created_at, id) WHERE status = 'queued';

ALTER TABLE visual_analysis_jobs
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_reason TEXT,
    ADD COLUMN IF NOT EXISTS materialization_started_at TIMESTAMPTZ;
ALTER TABLE visual_analysis_jobs
    DROP CONSTRAINT IF EXISTS visual_analysis_jobs_retry_count_check;
ALTER TABLE visual_analysis_jobs
    ADD CONSTRAINT visual_analysis_jobs_retry_count_check
    CHECK (retry_count >= 0 AND retry_count <= 1);
ALTER TABLE visual_analysis_jobs
    DROP CONSTRAINT IF EXISTS visual_analysis_jobs_status_check;
ALTER TABLE visual_analysis_jobs
    ADD CONSTRAINT visual_analysis_jobs_status_check
    CHECK (status IN ('created', 'queued', 'running', 'succeeded', 'failed', 'cancelled'));
CREATE INDEX IF NOT EXISTS idx_visual_analysis_jobs_queued
    ON visual_analysis_jobs (created_at, id) WHERE status = 'queued';
CREATE UNIQUE INDEX IF NOT EXISTS uq_visual_analysis_one_active_per_ingestion
    ON visual_analysis_jobs (ingestion_job_id) WHERE status IN ('queued', 'running');
