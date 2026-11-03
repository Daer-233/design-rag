CREATE TABLE IF NOT EXISTS manual_categories (
    id UUID PRIMARY KEY, name TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 120)
);
CREATE TABLE IF NOT EXISTS manual_entries (
    id UUID PRIMARY KEY, category_id UUID REFERENCES manual_categories(id),
    title TEXT NOT NULL, product TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
    family_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','published','superseded')),
    processing_status TEXT NOT NULL DEFAULT 'new', error_message TEXT,
    source_path TEXT, source_name TEXT, ingestion_job_id UUID,
    document_id UUID REFERENCES documents(id), document_version_id UUID REFERENCES document_versions(id),
    index_status TEXT NOT NULL DEFAULT 'pending', data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manual_entry_catalog ON manual_entries(status, category_id, product, version);
CREATE TABLE IF NOT EXISTS manual_assets (
    id UUID PRIMARY KEY, entry_id UUID NOT NULL REFERENCES manual_entries(id),
    name TEXT NOT NULL, storage_path TEXT NOT NULL, thumbnail_path TEXT NOT NULL,
    page_number INTEGER, metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
