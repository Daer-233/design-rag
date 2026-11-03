"""MinerU Precision API adapter.

The current implementation follows the documented v4 two-step workflow:
1. request a batch and a signed upload URL;
2. upload the source directly to that signed URL;
3. poll the batch result and download its ``full_zip_url`` when it is done.

No signed upload/download URL is persisted in the job audit JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import settings
from app.providers.parser.base import ParserProviderError, ParserStatus, ParserSubmission


class MinerUParserProvider:
    """Adapter for MinerU's documented Precision API v4."""

    provider_name = "mineru_precision_api"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        submit_url: str | None = None,
        status_url_template: str | None = None,
        result_url_template: str | None = None,
        model_version: str | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        supported_extensions: list[str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_token = api_token if api_token is not None else settings.mineru_api_token
        api_base_url = settings.mineru_api_base_url.rstrip("/")
        self.submit_url = (
            submit_url
            if submit_url is not None
            else (settings.mineru_submit_url or f"{api_base_url}/file-urls/batch")
        )
        self.status_url_template = (
            status_url_template
            if status_url_template is not None
            else (
                settings.mineru_status_url_template
                or f"{api_base_url}/extract-results/batch/{{batch_id}}"
            )
        )
        self.result_url_template = (
            result_url_template if result_url_template is not None else settings.mineru_result_url_template
        )
        self.model_version = model_version or settings.mineru_model_version
        self.enable_formula = settings.mineru_enable_formula if enable_formula is None else enable_formula
        self.enable_table = settings.mineru_enable_table if enable_table is None else enable_table
        self.language = language or settings.mineru_language
        self.supported_extensions = {
            item.lower().lstrip(".")
            for item in (supported_extensions if supported_extensions is not None else settings.mineru_supported_extensions)
        }
        self.transport = transport

    def is_configured(self) -> bool:
        return bool(self.api_token and self.submit_url and self.status_url_template)

    def submit(self, source_path: Path, *, file_name: str, content_type: str | None) -> ParserSubmission:
        """Create a one-file batch, then PUT the local source to MinerU's signed URL."""
        self._require_configuration("submit")
        self._require_supported_file_type(file_name)
        data_id = str(uuid4())
        payload: dict[str, object] = {
            "files": [{"name": file_name, "data_id": data_id}],
            "model_version": self.model_version,
        }
        payload["enable_formula"] = self.enable_formula
        payload["enable_table"] = self.enable_table
        if self.language:
            payload["language"] = self.language

        try:
            with self._client() as client:
                response = client.post(str(self.submit_url), headers=self._headers(), json=payload)
                response.raise_for_status()
                response_json = self._json_response(response, operation="submit")
                response_data = self._response_data(response_json, operation="submit")
                batch_id = self._first_string(response_data, "batch_id", "batchId")
                upload_url = self._first_url(response_data, "file_urls", "fileUrls")
                if not batch_id or not upload_url:
                    raise ParserProviderError(
                        "MinerU submit response did not contain batch_id and a signed file upload URL"
                    )

                # The URL is signed by MinerU/object storage. Deliberately do not pass the
                # MinerU API token to the object-storage upload endpoint.
                with source_path.open("rb") as source:
                    upload_response = client.put(upload_url, content=source)
                    upload_response.raise_for_status()
        except (OSError, httpx.HTTPError, ValueError) as error:
            if isinstance(error, ParserProviderError):
                raise
            raise ParserProviderError(f"MinerU submit failed: {type(error).__name__}") from error

        return ParserSubmission(
            # In the batch API, data_id is the durable per-file correlation ID.
            task_id=data_id,
            batch_id=batch_id,
            raw_response=self._sanitise_response(response_json),
        )

    def poll(self, task_id: str, *, batch_id: str | None = None) -> ParserStatus:
        """Poll one result item from MinerU's batch result endpoint."""
        self._require_configuration("poll")
        if not batch_id:
            raise ParserProviderError("MinerU Precision API polling requires the persisted provider_batch_id")
        url = str(self.status_url_template).format(task_id=task_id, batch_id=batch_id)
        try:
            with self._client() as client:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
                response_json = self._json_response(response, operation="status poll")
                response_data = self._response_data(response_json, operation="status poll")
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, ParserProviderError):
                raise
            raise ParserProviderError(f"MinerU status poll failed: {type(error).__name__}") from error

        result_item = self._matching_result(response_data, task_id)
        if result_item is None:
            return ParserStatus(
                status="processing",
                raw_response=self._sanitise_response(response_json),
            )

        raw_status = str(result_item.get("state") or result_item.get("status") or "processing").lower()
        status = self._normalise_status(raw_status)
        result_url = self._first_string(
            result_item,
            "full_zip_url",
            "fullZipUrl",
            "result_url",
            "resultUrl",
            "download_url",
            "downloadUrl",
        )
        if not result_url and status == "succeeded" and self.result_url_template:
            result_url = str(self.result_url_template).format(task_id=task_id, batch_id=batch_id)
        return ParserStatus(
            status=status,
            raw_response=self._sanitise_response(response_json),
            result_url=result_url,
            error_code=self._first_string(result_item, "error_code", "errorCode", "code"),
            error_message=self._first_string(result_item, "error_message", "errorMessage", "err_msg", "message"),
        )

    def download_result(self, result_url: str, destination: Path) -> Path:
        """Archive the result zip from the signed result URL without forwarding our API token."""
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with self._client(follow_redirects=True) as client:
                response = client.get(result_url)
                response.raise_for_status()
            destination.write_bytes(response.content)
            return destination
        except (OSError, httpx.HTTPError) as error:
            raise ParserProviderError(f"MinerU result download failed: {type(error).__name__}") from error

    def _client(self, *, follow_redirects: bool = False) -> httpx.Client:
        return httpx.Client(
            timeout=settings.mineru_poll_timeout_seconds,
            follow_redirects=follow_redirects,
            transport=self.transport,
        )

    def _require_configuration(self, operation: str) -> None:
        if not self.api_token:
            raise ParserProviderError("MinerU is not configured: MINERU_API_TOKEN is empty")
        if operation == "submit" and not self.submit_url:
            raise ParserProviderError("MinerU is not configured: MINERU_SUBMIT_URL is empty")
        if operation == "poll" and not self.status_url_template:
            raise ParserProviderError("MinerU is not configured: MINERU_STATUS_URL_TEMPLATE is empty")

    def _require_supported_file_type(self, file_name: str) -> None:
        extension = Path(file_name).suffix.lower().lstrip(".")
        if extension in self.supported_extensions:
            return
        supported = ", ".join(sorted(self.supported_extensions))
        raise ParserProviderError(
            f"MinerU Precision API is not configured to parse '.{extension}'. "
            f"Configured supported types: {supported}."
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    @staticmethod
    def _json_response(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        value = response.json()
        if not isinstance(value, dict):
            raise ParserProviderError(f"MinerU {operation} response was not a JSON object")
        code = value.get("code")
        if code not in (None, 0, "0"):
            message = MinerUParserProvider._first_string(value, "msg", "message") or "unknown MinerU API error"
            raise ParserProviderError(f"MinerU {operation} API error {code}: {message}")
        return value

    @staticmethod
    def _response_data(payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
        value = payload.get("data", payload)
        if not isinstance(value, dict):
            raise ParserProviderError(f"MinerU {operation} response data was not a JSON object")
        return value

    @staticmethod
    def _first_url(data: dict[str, Any], *names: str) -> str | None:
        for name in names:
            value = data.get(name)
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, str) and first:
                    return first
                if isinstance(first, dict):
                    candidate = MinerUParserProvider._first_string(first, "url", "file_url", "fileUrl")
                    if candidate:
                        return candidate
            elif isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _matching_result(data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        results = data.get("extract_result") or data.get("extractResults") or data.get("results")
        if not isinstance(results, list):
            return None
        for item in results:
            if isinstance(item, dict) and str(item.get("data_id") or item.get("dataId") or "") == task_id:
                return item
        # Every Design_RAG job creates one MinerU batch containing exactly one file.
        if len(results) == 1 and isinstance(results[0], dict):
            return results[0]
        return None

    @staticmethod
    def _sanitise_response(value: Any) -> Any:
        """Avoid storing short-lived object-storage signatures in PostgreSQL audit JSON."""
        if isinstance(value, list):
            return [MinerUParserProvider._sanitise_response(item) for item in value]
        if not isinstance(value, dict):
            return value
        sanitised: dict[str, Any] = {}
        for key, item in value.items():
            if "url" in key.lower():
                sanitised[key] = "[redacted signed URL]"
            else:
                sanitised[key] = MinerUParserProvider._sanitise_response(item)
        return sanitised

    @staticmethod
    def _first_string(data: dict[str, Any], *names: str) -> str | None:
        for name in names:
            value = data.get(name)
            if value is not None and str(value):
                return str(value)
        return None

    @staticmethod
    def _normalise_status(value: str) -> str:
        normalised = value.lower()
        if normalised in {"done", "completed", "complete", "succeeded", "success", "finished"}:
            return "succeeded"
        if normalised in {"failed", "error", "cancelled", "canceled"}:
            return "failed" if normalised not in {"cancelled", "canceled"} else "cancelled"
        if normalised in {"submitted", "queued", "pending", "waiting"}:
            return "submitted"
        return "processing"
