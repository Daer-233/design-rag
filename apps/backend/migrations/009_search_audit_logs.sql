-- Authoritative audit trail for every remote evidence-retrieval attempt.
-- This table intentionally stores operational retrieval feedback in PostgreSQL,
-- never in Qdrant. It contains only query and safe result identifiers, not source
-- paths, locators, or evidence content.
CREATE TABLE IF NOT EXISTS search_audit_logs (
    id UUID PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id UUID NOT NULL,
    actor_id TEXT NOT NULL,
    actor_display_name TEXT,
    identity_source TEXT NOT NULL CHECK (identity_source IN ('token_principal', 'requester_header')),
    principal_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    project_id UUID NOT NULL,
    query_text TEXT NOT NULL,
    query_sha256 TEXT NOT NULL,
    requested_evidence_top_k INTEGER,
    result_count INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
    result_chunk_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'denied', 'failed')),
    error_code TEXT,
    latency_ms DOUBLE PRECISION,
    client_ip TEXT,
    client_user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_search_audit_logs_requested_at
    ON search_audit_logs(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_audit_logs_project_requested_at
    ON search_audit_logs(project_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_audit_logs_actor_requested_at
    ON search_audit_logs(actor_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_audit_logs_outcome_requested_at
    ON search_audit_logs(outcome, requested_at DESC);
