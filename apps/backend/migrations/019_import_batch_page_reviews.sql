-- PDF page-by-page human review before external processing.
ALTER TABLE import_batches
    DROP CONSTRAINT IF EXISTS import_batches_status_check;
ALTER TABLE import_batches
    ADD CONSTRAINT import_batches_status_check
    CHECK (status IN (
        'uploading', 'preprocessing', 'awaiting_page_review', 'awaiting_approval', 'queued', 'processing',
        'materializing', 'indexing', 'succeeded', 'partially_succeeded', 'failed'
    ));

CREATE TABLE IF NOT EXISTS import_batch_page_reviews (
    id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES import_batch_items(id) ON DELETE CASCADE,
    source_page_number INTEGER NOT NULL CHECK (source_page_number > 0),
    preview_file_path TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL,
    preview_content_type TEXT NOT NULL DEFAULT 'image/jpeg',
    preview_size_bytes BIGINT NOT NULL CHECK (preview_size_bytes >= 0),
    preview_width INTEGER NOT NULL CHECK (preview_width > 0),
    preview_height INTEGER NOT NULL CHECK (preview_height > 0),
    selected_processor TEXT NOT NULL CHECK (selected_processor IN ('mineru', 'gpt_vision')) DEFAULT 'mineru',
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'confirmed')) DEFAULT 'pending',
    mineru_task_id UUID REFERENCES import_batch_tasks(id) ON DELETE SET NULL,
    vision_task_id UUID REFERENCES import_batch_tasks(id) ON DELETE SET NULL,
    task_status TEXT NOT NULL CHECK (task_status IN ('pending', 'awaiting_approval', 'queued', 'processing', 'succeeded', 'failed')) DEFAULT 'pending',
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(batch_id, item_id, source_page_number)
);
CREATE INDEX IF NOT EXISTS idx_import_batch_page_reviews_batch_item
    ON import_batch_page_reviews(batch_id, item_id, source_page_number);
