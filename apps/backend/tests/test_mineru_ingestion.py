import json
from pathlib import Path

import httpx
import pytest

from app.providers.parser.base import ParserProviderError
from app.providers.parser.mineru_provider import MinerUParserProvider
from app.services.ingestion_job_service import IngestionJobService


def test_mineru_precision_api_upload_poll_and_download(tmp_path: Path) -> None:
    source = tmp_path / "case.pdf"
    source.write_bytes(b"example-pdf")
    recorded: list[httpx.Request] = []
    data_id: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            data_id["value"] = payload["files"][0]["data_id"]
            assert payload["files"] == [{"name": "case.pdf", "data_id": data_id["value"]}]
            assert payload["model_version"] == "vlm"
            assert payload["enable_formula"] is True
            assert payload["enable_table"] is True
            assert payload["language"] == "ch"
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-123",
                        "file_urls": ["https://upload.example/signed?secret=do-not-store"],
                    },
                },
            )
        if request.method == "PUT":
            assert request.content == b"example-pdf"
            assert "authorization" not in request.headers
            return httpx.Response(200)
        if request.method == "GET" and request.url.host == "api.example":
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-123",
                        "extract_result": [
                            {
                                "data_id": data_id["value"],
                                "state": "done",
                                "full_zip_url": "https://download.example/result?secret=do-not-store",
                            }
                        ],
                    },
                },
            )
        if request.method == "GET" and request.url.host == "download.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"PK\x03\x04")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = MinerUParserProvider(
        api_token="test-token",
        submit_url="https://api.example/api/v4/file-urls/batch",
        status_url_template="https://api.example/api/v4/extract-results/batch/{batch_id}",
        transport=httpx.MockTransport(handler),
    )

    submission = provider.submit(source, file_name=source.name, content_type="application/pdf")
    assert submission.batch_id == "batch-123"
    assert submission.task_id == data_id["value"]
    assert submission.raw_response["data"]["file_urls"] == "[redacted signed URL]"

    parser_status = provider.poll(submission.task_id, batch_id=submission.batch_id)
    assert parser_status.status == "succeeded"
    assert parser_status.result_url == "https://download.example/result?secret=do-not-store"
    assert parser_status.raw_response["data"]["extract_result"][0]["full_zip_url"] == "[redacted signed URL]"

    downloaded = provider.download_result(parser_status.result_url, tmp_path / "result.zip")
    assert downloaded.read_bytes() == b"PK\x03\x04"
    assert [request.method for request in recorded] == ["POST", "PUT", "GET", "GET"]


def test_mineru_precision_api_accepts_xlsx() -> None:
    provider = MinerUParserProvider(api_token="REPLACE_ME")
    provider._require_supported_file_type("case.xlsx")


def test_mineru_precision_api_rejects_unknown_extension() -> None:
    provider = MinerUParserProvider(api_token="REPLACE_ME")
    with pytest.raises(ParserProviderError, match="not configured to parse '.dwg'"):
        provider._require_supported_file_type("case.dwg")


def test_mineru_status_is_normalised() -> None:
    assert MinerUParserProvider._normalise_status("done") == "succeeded"
    assert MinerUParserProvider._normalise_status("completed") == "succeeded"
    assert MinerUParserProvider._normalise_status("queued") == "submitted"
    assert MinerUParserProvider._normalise_status("running") == "processing"
    assert MinerUParserProvider._normalise_status("error") == "failed"


def test_json_metadata_requires_object() -> None:
    with pytest.raises(Exception, match="JSON object"):
        IngestionJobService._json_object("[]", "document_metadata_json")
    assert IngestionJobService._json_object('{"topic":"materials"}', "document_metadata_json") == {
        "topic": "materials"
    }


