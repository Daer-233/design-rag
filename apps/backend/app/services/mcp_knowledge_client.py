"""Minimal synchronous MCP client used by the local Chat Agent workflow."""

from __future__ import annotations

import json
from threading import Lock
from typing import Any

import httpx


class McpKnowledgeError(RuntimeError):
    pass


class McpKnowledgeClient:
    """Call the local Design_RAG Streamable HTTP MCP server without exposing its API token."""

    def __init__(
        self,
        server_url: str,
        *,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server_url = server_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._counter = 0
        self._lock = Lock()

    def list_projects(self) -> list[dict[str, Any]]:
        payload = self.call_tool("list_projects", {})
        projects = payload.get("projects", []) if isinstance(payload, dict) else []
        return [dict(item) for item in projects if isinstance(item, dict)] if isinstance(projects, list) else []

    def list_project_documents(self, project_id: str) -> list[dict[str, Any]]:
        payload = self.call_tool("list_project_documents", {"project_id": project_id})
        documents = payload.get("documents", []) if isinstance(payload, dict) else []
        return [dict(item) for item in documents if isinstance(item, dict)] if isinstance(documents, list) else []

    def search(
        self,
        *,
        query: str,
        project_id: str | None,
        evidence_top_k: int | None,
        filters: dict[str, Any] | None,
        include_images: bool = True,
        image_mode: str = "metadata",
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "query": query,
            "evidence_top_k": evidence_top_k or 8,
        }
        if project_id:
            arguments["project_id"] = project_id
        if filters:
            arguments["filters"] = filters
        arguments["include_images"] = include_images
        arguments["image_mode"] = image_mode
        payload = self.call_tool("search_knowledge_base", arguments)
        if not isinstance(payload, dict):
            raise McpKnowledgeError("MCP search_knowledge_base returned a non-object result")
        return payload

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        request_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    self.server_url,
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=request,
                )
            response.raise_for_status()
            envelope = self._decode_response(response)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
            raise McpKnowledgeError(f"Design_RAG MCP request failed: {error}") from error
        if envelope.get("error"):
            raise McpKnowledgeError(f"Design_RAG MCP error: {envelope['error']}")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise McpKnowledgeError("Design_RAG MCP returned no tool result")
        if result.get("isError"):
            message = self._content_text(result) or "MCP tool failed"
            raise McpKnowledgeError(message)
        if "structuredContent" in result:
            return result["structuredContent"]
        text = self._content_text(result)
        return json.loads(text) if text else {}

    def _next_id(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" not in content_type:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("MCP JSON response is not an object")
            return payload
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if isinstance(payload, dict):
                return payload
        raise ValueError("MCP event stream contained no JSON-RPC data event")

    @staticmethod
    def _content_text(result: dict[str, Any]) -> str:
        content = result.get("content")
        if not isinstance(content, list):
            return ""
        return "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()


class McpSearchServiceAdapter:
    """Preserve SearchService.search so the recovered workflow can use MCP unchanged."""

    def __init__(self, client: McpKnowledgeClient) -> None:
        self.client = client

    def search(
        self,
        *,
        query: str,
        project_id: str | None,
        principal_raw: object,
        evidence_top_k: int | None,
        retrieval_filters: dict[str, Any] | None = None,
        redact_source_details: bool = True,
    ) -> dict[str, Any]:
        del principal_raw, redact_source_details
        return self.client.search(
            query=query,
            project_id=project_id,
            evidence_top_k=evidence_top_k,
            filters=retrieval_filters,
            include_images=True,
            image_mode="metadata",
        )

