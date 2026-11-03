-- Durable, explicitly approved parsing schedules for import batches.
ALTER TABLE import_batches
    ADD COLUMN IF NOT EXISTS schedule_mode TEXT NOT NULL DEFAULT 'immediate'
        CHECK (schedule_mode IN ('immediate', 'nightly', 'custom')),
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS schedule_requested_at TIMESTAMPTZ;

ALTER TABLE import_batches
    DROP CONSTRAINT IF EXISTS import_batches_status_check;
ALTER TABLE import_batches
    ADD CONSTRAINT import_batches_status_check
    CHECK (status IN (
        'uploading', 'preprocessing', 'awaiting_page_review', 'awaiting_approval',
        'scheduled', 'scheduling', 'queued', 'processing', 'materializing', 'indexing',
        'succeeded', 'partially_succeeded', 'failed'
    ));

ALTER TABLE import_batch_tasks
    DROP CONSTRAINT IF EXISTS import_batch_tasks_status_check;
ALTER TABLE import_batch_tasks
    ADD CONSTRAINT import_batch_tasks_status_check
    CHECK (status IN ('awaiting_approval', 'scheduled', 'queued', 'processing', 'succeeded', 'failed', 'skipped'));

ALTER TABLE import_batch_page_reviews
    DROP CONSTRAINT IF EXISTS import_batch_page_reviews_task_status_check;
ALTER TABLE import_batch_page_reviews
    ADD CONSTRAINT import_batch_page_reviews_task_status_check
    CHECK (task_status IN ('pending', 'awaiting_approval', 'scheduled', 'queued', 'processing', 'succeeded', 'failed'));

CREATE INDEX IF NOT EXISTS idx_import_batches_scheduled_due
    ON import_batches(scheduled_for ASC)
    WHERE status IN ('scheduled', 'scheduling');
