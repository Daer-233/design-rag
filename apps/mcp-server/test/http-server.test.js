import test from "node:test";
import assert from "node:assert/strict";

import { createHttpServer } from "../src/server.js";

function decodeEnvelope(responseText) {
  for (const line of responseText.split(/\r?\n/)) {
    if (line.startsWith("data:")) return JSON.parse(line.slice(5).trim());
  }
  return JSON.parse(responseText);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/json, text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  assert.equal(response.status, 200, text);
  return decodeEnvelope(text);
}

test("streamable HTTP exposes initialize, tools/list, and read-only tool calls", async (t) => {
  const calls = [];
  const apiClient = {
    async listProjects() {
      calls.push("listProjects");
      return { projects: [{ id: "bae924de-0c3a-42ab-8ed7-aec3fe1ae95d", name: "华润顶层会所" }] };
    },
    async listProjectDocuments(projectId) {
      calls.push(["listProjectDocuments", projectId]);
      return { project_id: projectId, documents: [] };
    },
    async search(payload) {
      calls.push(["search", payload]);
      return { results: [], query: { raw: payload.query } };
    },
    async getCapabilities() {
      return { evidence_types: ["document_confirmed", "vision_inferred", "candidate_match", "unknown"] };
    },
  };
  const server = createHttpServer({ apiClient });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const url = `http://127.0.0.1:${address.port}/mcp`;

  const initialized = await postJson(url, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "design-rag-test", version: "1.0.0" },
    },
  });
  assert.equal(initialized.result.serverInfo.name, "design-rag");

  const listed = await postJson(url, { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
  const names = listed.result.tools.map((tool) => tool.name);
  assert.ok(names.includes("list_projects"));
  assert.ok(names.includes("search_knowledge_base"));
  assert.ok(names.includes("search_materials"));

  const projects = await postJson(url, {
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: { name: "list_projects", arguments: {} },
  });
  assert.equal(projects.result.structuredContent.projects[0].name, "华润顶层会所");

  const search = await postJson(url, {
    jsonrpc: "2.0",
    id: 4,
    method: "tools/call",
    params: {
      name: "search_knowledge_base",
      arguments: {
        query: "电视背景墙材料",
        project_id: "bae924de-0c3a-42ab-8ed7-aec3fe1ae95d",
        evidence_top_k: 8,
      },
    },
  });
  assert.ok(search.result?.structuredContent, JSON.stringify(search));
  assert.deepEqual(search.result.structuredContent.results, []);
  assert.equal(calls[0], "listProjects");
  assert.equal(calls[1][0], "search");
});