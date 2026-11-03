import { z } from "zod";

const DEFAULT_BASE_URL = "http://127.0.0.1:8030";

export class DesignRagApiError extends Error {
  constructor(message, { status = 500, details = null } = {}) {
    super(message);
    this.name = "DesignRagApiError";
    this.status = status;
    this.details = details;
  }
}

export class DesignRagApiClient {
  constructor({ baseUrl, token, fetchImpl = fetch, requesterId = "design-rag-mcp" } = {}) {
    this.baseUrl = String(baseUrl || process.env.DESIGN_RAG_API_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.token = token || process.env.DESIGN_RAG_API_TOKEN || "";
    this.fetchImpl = fetchImpl;
    this.requesterId = requesterId;
  }

  assertConfigured() {
    if (!this.token) {
      throw new DesignRagApiError("DESIGN_RAG_API_TOKEN is not configured", { status: 503 });
    }
  }

  async request(path, { method = "GET", body } = {}) {
    this.assertConfigured();
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Design-RAG-Requester-ID": this.requesterId,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`;
      throw new DesignRagApiError(`Design_RAG API request failed: ${detail}`, {
        status: response.status,
        details: payload,
      });
    }
    return payload;
  }

  async requestBinary(path) {
    this.assertConfigured();
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${this.token}`,
        Accept: "image/*, application/octet-stream",
        "X-Design-RAG-Requester-ID": this.requesterId,
      },
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload && typeof payload.detail === "string") detail = payload.detail;
      } catch {
        // Keep the HTTP status when the API did not return JSON.
      }
      throw new DesignRagApiError(`Design_RAG image request failed: ${detail}`, { status: response.status });
    }
    return {
      data: Buffer.from(await response.arrayBuffer()),
      contentType: response.headers.get("content-type") || "application/octet-stream",
    };
  }

  listProjects() {
    return this.request("/api/v1/projects");
  }

  listProjectDocuments(projectId) {
    return this.request(`/api/v1/projects/${encodeURIComponent(projectId)}/documents`);
  }

  getCapabilities() {
    return this.request("/api/v1/retrieval-capabilities");
  }

  search({ query, projectId = null, evidenceTopK = 8, filters = null, includeImages = true, imageMode = "metadata" }) {
    const body = { query, evidence_top_k: evidenceTopK, include_images: includeImages, image_mode: imageMode };
    if (projectId) body.project_id = projectId;
    if (filters && Object.keys(filters).length) body.filters = filters;
    return this.request("/api/v1/search", { method: "POST", body });
  }

  getDocumentPage(projectId, documentId, pageNumber) {
    const query = new URLSearchParams({ project_id: projectId });
    return this.request(`/api/v1/mcp/documents/${encodeURIComponent(documentId)}/pages/${encodeURIComponent(pageNumber)}?${query}`);
  }

  listChunkImages(projectId, chunkId) {
    const query = new URLSearchParams({ project_id: projectId });
    return this.request(`/api/v1/mcp/chunks/${encodeURIComponent(chunkId)}/images?${query}`);
  }

  readImageAsset(projectId, assetId, variant = "original") {
    const query = new URLSearchParams({ project_id: projectId });
    return this.requestBinary(`/api/v1/mcp/assets/${encodeURIComponent(assetId)}/${encodeURIComponent(variant)}?${query}`);
  }

  createHarnessRun(payload) { return this.request('/api/v1/admin/harness/runs', { method: 'POST', body: payload }); }
  getHarnessRun(id) { return this.request(`/api/v1/admin/harness/runs/${encodeURIComponent(id)}`); }
  createImageGenerationJob(payload) { return this.request('/api/v1/admin/image-generation/jobs', { method: 'POST', body: payload }); }
  getImageGenerationJob(id) { return this.request(`/api/v1/admin/image-generation/jobs/${encodeURIComponent(id)}`); }
  confirmImageGenerationJob(id) { return this.request(`/api/v1/admin/image-generation/jobs/${encodeURIComponent(id)}/confirm`, { method: 'POST' }); }

  imageAssetUrl(projectId, assetId, variant = "thumbnail") {
    const query = new URLSearchParams({ project_id: projectId });
    return `${this.baseUrl}/api/v1/mcp/assets/${encodeURIComponent(assetId)}/${encodeURIComponent(variant)}?${query}`;
  }
}

export function normalizeProjectLabel(value) {
  return String(value || "")
    .trim()
    .replace(/^\s*\d+\s*/, "")
    .replace(/[\s_-]+/g, "")
    .toLocaleLowerCase("zh-CN");
}

export function resolveProjectCandidates(projects, reference) {
  const raw = String(reference || "").trim();
  const normalized = normalizeProjectLabel(raw);
  if (!raw) return [];
  const exact = projects.filter((project) =>
    [project.id, project.external_key, project.name].some((value) => String(value || "").trim().toLocaleLowerCase("zh-CN") === raw.toLocaleLowerCase("zh-CN")),
  );
  if (exact.length) return exact;
  return projects.filter((project) => {
    const aliases = [project.name, project.external_key].map(normalizeProjectLabel).filter(Boolean);
    return aliases.some((alias) => alias === normalized || alias.includes(normalized) || normalized.includes(alias));
  });
}

export function compactTextResult(data) {
  return JSON.stringify(data, null, 2);
}

export const filterSchema = z
  .object({
    file_types: z.array(z.string().min(1)).max(20).optional(),
    chunk_types: z.array(z.string().min(1)).max(30).optional(),
    content_origins: z.array(z.string().min(1)).max(20).optional(),
    page_start: z.number().int().positive().optional(),
    page_end: z.number().int().positive().optional(),
  })
  .optional();
