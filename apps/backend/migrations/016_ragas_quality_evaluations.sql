-- Durable, auditable RAGAS quality-evaluation suites and runs.
-- Evaluation inputs/results are limited to test questions, reference answers, generated answers,
-- and already-authorized retrieved snippets; no source files or archive paths are stored here.

CREATE TABLE IF NOT EXISTS rag_evaluation_suites (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_suites_project_updated
    ON rag_evaluation_suites(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS rag_evaluation_cases (
    id UUID PRIMARY KEY,
    suite_id UUID NOT NULL REFERENCES rag_evaluation_suites(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_cases_suite_order
    ON rag_evaluation_cases(suite_id, enabled DESC, sort_order ASC, created_at ASC);

CREATE TABLE IF NOT EXISTS rag_evaluation_runs (
    id UUID PRIMARY KEY,
    suite_id UUID NOT NULL REFERENCES rag_evaluation_suites(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'partially_succeeded', 'failed')),
    evaluator_name TEXT NOT NULL DEFAULT 'ragas',
    evaluator_version TEXT,
    model_id TEXT NOT NULL,
    evidence_top_k INTEGER NOT NULL CHECK (evidence_top_k BETWEEN 1 AND 20),
    approval_record JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_runs_suite_created
    ON rag_evaluation_runs(suite_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_runs_status_created
    ON rag_evaluation_runs(status, created_at ASC);

CREATE TABLE IF NOT EXISTS rag_evaluation_case_results (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES rag_evaluation_runs(id) ON DELETE CASCADE,
    case_id UUID NOT NULL REFERENCES rag_evaluation_cases(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    generated_answer TEXT,
    retrieved_contexts JSONB NOT NULL DEFAULT '[]'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    duration_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_case_results_run
    ON rag_evaluation_case_results(run_id, created_at ASC);
