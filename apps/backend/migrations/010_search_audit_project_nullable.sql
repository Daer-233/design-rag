-- Cross-project retrieval has no single project UUID to attach to an audit row.
ALTER TABLE search_audit_logs
    ALTER COLUMN project_id DROP NOT NULL;
