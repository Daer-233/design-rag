-- MinerU Precision API currently accepts model_version=vlm (one "l"), not vllm.
-- Keep the route label truthful while preserving the separate legacy pipeline route.
DO $$
DECLARE
    route_constraint_name TEXT;
BEGIN
    SELECT conname INTO route_constraint_name
    FROM pg_constraint
    WHERE conrelid = 'ingestion_jobs'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%processing_route%';

    IF route_constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ingestion_jobs DROP CONSTRAINT %I', route_constraint_name);
    END IF;
END $$;

UPDATE ingestion_jobs
SET processing_route = CASE processing_route
        WHEN 'mineru_vllm' THEN 'mineru_vlm'
        WHEN 'mineru_vllm_then_vision' THEN 'mineru_vlm_then_vision'
        ELSE processing_route
    END,
    route_reason = replace(route_reason, 'vllm', 'vlm')
WHERE processing_route IN ('mineru_vllm', 'mineru_vllm_then_vision')
   OR route_reason LIKE '%vllm%';

ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_processing_route_check
    CHECK (processing_route IN (
        'mineru_vlm', 'vision', 'mineru_vlm_then_vision', 'legacy_mineru_pipeline'
    ));
