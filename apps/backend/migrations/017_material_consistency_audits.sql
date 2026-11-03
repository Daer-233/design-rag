-- Durable Admin material-consistency audits. Source archive paths remain in existing local asset tables;
-- these tables retain only source identifiers, structural visual results and document evidence references.

CREATE TABLE IF NOT EXISTS material_consistency_audits (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN (
        'waiting_for_visual', 'queued', 'running', 'succeeded', 'partially_succeeded', 'failed', 'cancelled'
    )),
    selected_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_material_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    approval_record JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_name TEXT,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    cancel_requested_at TIMESTAMPTZ,
    cancel_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_material_consistency_audits_project_created
    ON material_consistency_audits(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_material_consistency_audits_status_created
    ON material_consistency_audits(status, created_at ASC);

CREATE TABLE IF NOT EXISTS material_consistency_items (
    id UUID PRIMARY KEY,
    audit_id UUID NOT NULL REFERENCES material_consistency_audits(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('ingestion_job', 'image_asset')),
    source_id UUID NOT NULL,
    ingestion_job_id UUID REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    source_file_name TEXT NOT NULL,
    page_number INTEGER,
    component_kind TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_region JSONB NOT NULL DEFAULT '{}'::jsonb,
    visual_material JSONB NOT NULL DEFAULT '{}'::jsonb,
    material_requirement JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict TEXT NOT NULL CHECK (verdict IN ('consistent', 'suspected_inconsistent', 'unable_to_confirm', 'missing_materials')),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    ai_judgement JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_material_consistency_items_audit_source
    ON material_consistency_items(audit_id, source_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_material_consistency_items_audit_verdict
    ON material_consistency_items(audit_id, verdict);
