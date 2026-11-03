"""Small service for retrieval-audit persistence that must never break a search."""

from __future__ import annotations

from app.repositories.search_audit_repository import SearchAuditRepository, SearchAuditWrite


class SearchAuditService:
    def __init__(self, repository: SearchAuditRepository) -> None:
        self.repository = repository

    def record_safely(self, item: SearchAuditWrite) -> None:
        """Audit write failures are operationally visible in logs but never alter retrieval output."""
        try:
            self.repository.record(item)
        except Exception:
            # Search remains available even if the optional optimization telemetry is temporarily unavailable.
            # The canonical source data and retrieval index are unaffected.
            return
