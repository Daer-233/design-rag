-- Cache analysis for extracted PDF/PPT image assets so approved visual analysis is not resent.
ALTER TABLE image_assets
    ADD COLUMN IF NOT EXISTS vision_analysis JSONB,
    ADD COLUMN IF NOT EXISTS vision_status TEXT,
    ADD COLUMN IF NOT EXISTS vision_provider_name TEXT,
    ADD COLUMN IF NOT EXISTS vision_model_id TEXT,
    ADD COLUMN IF NOT EXISTS vision_prompt_version TEXT,
    ADD COLUMN IF NOT EXISTS vision_updated_at TIMESTAMPTZ;

ALTER TABLE image_assets
    DROP CONSTRAINT IF EXISTS image_assets_vision_status_check;

ALTER TABLE image_assets
    ADD CONSTRAINT image_assets_vision_status_check
    CHECK (vision_status IS NULL OR vision_status IN ('succeeded', 'failed'));

CREATE INDEX IF NOT EXISTS idx_image_assets_vision_status
    ON image_assets(vision_status)
    WHERE vision_status IS NOT NULL;
