from __future__ import annotations

import json

import httpx

from app.services.mcp_knowledge_client import McpKnowledgeClient, McpSearchServiceAdapter


def _sse_result(result: dict[str, object]) -> httpx.Response:
    body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result})}\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


def test_mcp_client_reads_structured_content_from_streamable_http() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return _sse_result(
            {
                "content": [{"type": "text", "text": "{}"}],
                "structuredContent": {"projects": [{"id": "project-1", "name": "绿城下沙"}]},
            }
        )

    client = McpKnowledgeClient("http://mcp.test/mcp", transport=httpx.MockTransport(handler))
    assert client.list_projects()[0]["name"] == "绿城下沙"
    assert calls[0]["method"] == "tools/call"
    assert calls[0]["params"]["name"] == "list_projects"


def test_mcp_search_adapter_preserves_workflow_search_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        arguments = payload["params"]["arguments"]
        assert arguments["project_id"] == "11111111-1111-1111-1111-111111111111"
        assert arguments["filters"]["chunk_types"] == ["material_item"]
        return _sse_result(
            {
                "structuredContent": {"results": [], "pipeline": {"reason": "no_accessible_evidence"}},
                "content": [],
            }
        )

    adapter = McpSearchServiceAdapter(
        McpKnowledgeClient("http://mcp.test/mcp", transport=httpx.MockTransport(handler))
    )
    result = adapter.search(
        query="电视背景墙是什么材料",
        project_id="11111111-1111-1111-1111-111111111111",
        principal_raw=object(),
        evidence_top_k=8,
        retrieval_filters={"chunk_types": ["material_item"]},
        redact_source_details=True,
    )
    assert result["results"] == []
