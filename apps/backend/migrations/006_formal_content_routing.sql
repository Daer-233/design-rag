-- Formal content-routing metadata. Documents go to MinerU vllm; ordinary images
-- go directly to the explicitly authorised vision provider; text-heavy images go
-- MinerU vllm OCR first and then can be submitted to vision.
ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS processing_route TEXT NOT NULL DEFAULT 'mineru_vllm'
    CHECK (processing_route IN ('mineru_vllm', 'vision', 'mineru_vllm_then_vision'));

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS route_reason TEXT NOT NULL DEFAULT '';

-- Direct GPT analysis of ordinary images must not require a preceding MinerU
-- materialization. OCR-plus-vision jobs still retain their source version when present.
ALTER TABLE visual_analysis_jobs
    ALTER COLUMN source_document_version_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_processing_route
    ON ingestion_jobs(processing_route);
