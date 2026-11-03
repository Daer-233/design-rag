-- Anonymous local Agent conversations, runs, event log and memory summaries.
-- This phase intentionally has no login: the HttpOnly anonymous cookie identifies
-- an isolated local browser profile. PostgreSQL remains authoritative.

CREATE TABLE IF NOT EXISTS agent_anonymous_clients (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_conversations (
    id UUID PRIMARY KEY,
    anonymous_client_id UUID NOT NULL REFERENCES agent_anonymous_clients(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '新对话',
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_client_updated
    ON agent_conversations(anonymous_client_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    run_id UUID,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    content_origin TEXT NOT NULL DEFAULT 'user' CHECK (content_origin IN ('user', 'knowledge_base', 'general_llm', 'system')),
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_conversation_created
    ON agent_messages(conversation_id, created_at ASC);

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    request_id TEXT,
    query_text TEXT NOT NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    intent_label TEXT,
    intent_confidence DOUBLE PRECISION,
    rewritten_query TEXT,
    reasoning_summary TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(conversation_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation_created
    ON agent_runs(conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_workflow_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    node_name TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_events_run_id
    ON agent_workflow_events(run_id, id ASC);
