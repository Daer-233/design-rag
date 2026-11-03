# Work Knowledge Assistant — experimental direct Runner

This optional LangBot Protocol v1 Runner queries the local knowledge service and preserves original text, citations and inline thumbnails. It does not invoke a model or change knowledge records.

The active deployment uses the existing **CodexAgent** processor with **HTTP RAG Connector**, as subsequently requested by the user. This direct Runner is retained as an uninstalled alternative; it is not the current chat processor.

- Component: `components/runner/knowledge.yaml`, usage `agent`.
- Runtime contract tested with the installed `langbot-plugin==0.7.16` SDK: 20 tests passed.
- Local endpoint: `http://host.docker.internal:8060` in Docker, `http://127.0.0.1:8060` on the host.
- Queries always use `source_only=true`. No ingestion or external parsing is performed.
- Conversation continuation is scoped by the Host and actor, expires after 30 minutes, and never uses instance caches.

See [active integration guide](../../docs/langbot-work-knowledge.md).
