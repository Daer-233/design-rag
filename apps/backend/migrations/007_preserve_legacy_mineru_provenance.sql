-- Preserve historical provenance: jobs submitted before the formal vllm routing
-- rollout were created under the prior default MinerU pipeline profile. They must
-- not be relabelled as vllm merely because the routing column was introduced later.
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

ALTER TABLE ingestion_jobs
    ADD CONSTRAINT ingestion_jobs_processing_route_check
    CHECK (processing_route IN (
        'mineru_vllm', 'vision', 'mineru_vllm_then_vision', 'legacy_mineru_pipeline'
    ));

UPDATE ingestion_jobs
SET processing_route = 'legacy_mineru_pipeline',
    route_reason = 'historical MinerU job submitted before the cloud vllm routing rollout'
WHERE submitted_at IS NOT NULL
  AND processing_route = 'mineru_vllm';
