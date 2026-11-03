-- Hybrid page pipeline: MinerU extracts text/tables/images, then GPT analyzes only extracted images.
ALTER TABLE import_batch_page_reviews
    DROP CONSTRAINT IF EXISTS import_batch_page_reviews_selected_processor_check;
ALTER TABLE import_batch_page_reviews
    ADD CONSTRAINT import_batch_page_reviews_selected_processor_check
    CHECK (selected_processor IN ('mineru', 'gpt_vision', 'mineru_then_gpt'));

ALTER TABLE import_batches
    ADD COLUMN IF NOT EXISTS mineru_approved BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS vision_approved BOOLEAN NOT NULL DEFAULT FALSE;
