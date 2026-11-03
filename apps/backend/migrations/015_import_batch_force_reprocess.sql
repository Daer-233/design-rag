-- Allow an operator to explicitly create a new, auditable ingestion attempt for an unchanged source.
ALTER TABLE import_batches
    ADD COLUMN IF NOT EXISTS force_reprocess BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN import_batches.force_reprocess IS
    'When true, identical source SHA-256 files create new jobs rather than duplicate_skipped items.';
