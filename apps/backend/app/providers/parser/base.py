"""External document parser provider contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ParserProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParserSubmission:
    task_id: str
    batch_id: str | None
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class ParserStatus:
    status: str
    raw_response: dict[str, Any]
    result_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ParserProvider(Protocol):
    provider_name: str

    def submit(self, source_path: Path, *, file_name: str, content_type: str | None) -> ParserSubmission: ...

    def poll(self, task_id: str, *, batch_id: str | None = None) -> ParserStatus: ...

    def download_result(self, result_url: str, destination: Path) -> Path: ...
