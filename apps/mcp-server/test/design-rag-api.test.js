import test from "node:test";
import assert from "node:assert/strict";

import { DesignRagApiClient, resolveProjectCandidates } from "../src/design-rag-api.js";

test("resolveProjectCandidates matches numbered Chinese project names", () => {
  const projects = [
    { id: "bae924de-0c3a-42ab-8ed7-aec3fe1ae95d", name: "01 绿城下沙", external_key: "greentown-xiasha-01" },
    { id: "22222222-2222-2222-2222-222222222222", name: "杭州中海", external_key: "zhonghai" },
  ];
  assert.deepEqual(resolveProjectCandidates(projects, "绿城下沙"), [projects[0]]);
  assert.deepEqual(resolveProjectCandidates(projects, projects[0].id), [projects[0]]);
});

test("DesignRagApiClient keeps the bearer token out of URLs and request bodies", async () => {
  const requests = [];
  const client = new DesignRagApiClient({
    baseUrl: "http://example.test",
    token: "secret-token",
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      return new Response(JSON.stringify({ projects: [] }), { status: 200, headers: { "content-type": "application/json" } });
    },
  });
  await client.listProjects();
  assert.equal(requests[0].url, "http://example.test/api/v1/projects");
  assert.equal(requests[0].init.headers.Authorization, "Bearer secret-token");
  assert.equal(requests[0].init.body, undefined);
  assert.equal(requests[0].url.includes("secret-token"), false);
});
