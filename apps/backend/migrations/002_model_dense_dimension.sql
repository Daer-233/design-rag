ALTER TABLE model_versions
    ADD COLUMN IF NOT EXISTS dense_dimension INTEGER CHECK (dense_dimension > 0);
