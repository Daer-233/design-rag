-- Authoritative provenance for derived files, PDF/PPTX image regions, and agent-specific read-only access.
-- PostgreSQL remains the system of record; Qdrant stores only rebuildable text/vector payloads.

ALTER TABLE document_versions
    ADD COLUMN IF NOT EXISTS source_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS source_storage_key TEXT,
    ADD COLUMN IF NOT EXISTS source_file_size_bytes BIGINT CHECK (source_file_size_bytes IS NULL OR source_file_size_bytes >= 0),
    ADD COLUMN IF NOT EXISTS parse_status TEXT NOT NULL DEFAULT 'materialized';

ALTER TABLE document_assets
    ADD COLUMN IF NOT EXISTS source_asset_id UUID REFERENCES document_assets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS derivation_type TEXT,
    ADD COLUMN IF NOT EXISTS page_start INTEGER,
    ADD COLUMN IF NOT EXISTS page_end INTEGER,
    ADD COLUMN IF NOT EXISTS generator_version TEXT;

CREATE TABLE IF NOT EXISTS document_asset_derivations (
    id UUID PRIMARY KEY,
    parent_asset_id UUID NOT NULL REFERENCES document_assets(id) ON DELETE CASCADE,
    child_asset_id UUID NOT NULL REFERENCES document_assets(id) ON DELETE CASCADE,
    derivation_type TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    source_sha256 TEXT,
    derivative_sha256 TEXT,
    generator_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(parent_asset_id, child_asset_id, derivation_type)
);

CREATE TABLE IF NOT EXISTS image_assets (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID REFERENCES document_versions(id) ON DELETE SET NULL,
    document_asset_id UUID REFERENCES document_assets(id) ON DELETE SET NULL,
    parent_chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
    child_chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
    page_number INTEGER,
    bbox JSONB NOT NULL DEFAULT '{}'::jsonb,
    image_hash TEXT,
    perceptual_hash TEXT,
    image_classification TEXT NOT NULL CHECK (image_classification IN (
        'material_board', 'material_swatch', 'product_image', 'equipment_image', 'furniture_image',
        'table_image', 'drawing_legend', 'floor_plan', 'elevation', 'section', 'detail_drawing',
        'rendering', 'chart', 'decorative', 'unknown'
    )),
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'pending', 'metadata_saved', 'ocr_completed', 'structured_completed', 'vision_pending_approval',
        'vision_completed', 'skipped_decorative', 'needs_review', 'failed'
    )),
    source_storage_key TEXT,
    derivative_storage_key TEXT,
    adjacent_text TEXT NOT NULL DEFAULT '',
    caption_text TEXT NOT NULL DEFAULT '',
    table_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_image_assets_document_page ON image_assets(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_image_assets_version ON image_assets(document_version_id);
CREATE INDEX IF NOT EXISTS idx_image_assets_parent_chunk ON image_assets(parent_chunk_id);
CREATE INDEX IF NOT EXISTS idx_image_assets_hash ON image_assets(image_hash);
CREATE INDEX IF NOT EXISTS idx_image_assets_phash ON image_assets(perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_image_assets_status ON image_assets(processing_status);

-- Token values are stored only as SHA-256 hashes. The raw token stays in environment configuration.
CREATE TABLE IF NOT EXISTS agent_api_tokens (
    id UUID PRIMARY KEY,
    token_sha256 TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    display_name TEXT,
    project_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_all_projects BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_api_tokens_status ON agent_api_tokens(status);
