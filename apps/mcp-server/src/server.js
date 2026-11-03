import http from "node:http";
import { McpServer } from "@modelcontextprotocol/server";
import { NodeStreamableHTTPServerTransport } from "@modelcontextprotocol/node";
import { z } from "zod";

import {
  DesignRagApiClient,
  compactTextResult,
  filterSchema,
  resolveProjectCandidates,
} from "./design-rag-api.js";

const HOST = process.env.DESIGN_RAG_MCP_HOST || "0.0.0.0";
const PORT = Number.parseInt(process.env.DESIGN_RAG_MCP_PORT || "8040", 10);
const MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024;

function toolResult(data, extraContent = []) {
  return {
    content: [{ type: "text", text: compactTextResult(data) }, ...extraContent],
    structuredContent: data,
  };
}

function toolError(error) {
  const message = error instanceof Error ? error.message : String(error);
  return {
    isError: true,
    content: [{ type: "text", text: message }],
    structuredContent: { error: message },
  };
}

function imageReference(apiClient, projectId, assetId, variant = "thumbnail") {
  return {
    asset_id: assetId,
    project_id: projectId,
    variant,
    url: apiClient.imageAssetUrl(projectId, assetId, variant),
    resource_uri: `design-rag://assets/${assetId}`,
  };
}

export function createDesignRagMcpServer({ apiClient = new DesignRagApiClient() } = {}) {
  const server = new McpServer({ name: "design-rag", version: "0.1.0" });

  server.registerTool(
    "list_projects",
    {
      title: "查询知识库项目",
      description: "列出当前 Agent Token 可访问且已有有效知识文档的所有项目。检索前应优先调用。",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => {
      try {
        return toolResult(await apiClient.listProjects());
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "resolve_project",
    {
      title: "解析项目名称",
      description: "根据项目名称、项目 external key 或 UUID 返回匹配项目。不会在多个候选中偷偷选择。",
      inputSchema: z.object({ reference: z.string().min(1).max(500) }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ reference }) => {
      try {
        const payload = await apiClient.listProjects();
        const projects = Array.isArray(payload?.projects) ? payload.projects : [];
        const matches = resolveProjectCandidates(projects, reference);
        return toolResult({ reference, match_count: matches.length, resolved: matches.length === 1, matches });
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "list_project_documents",
    {
      title: "查询项目文档",
      description: "列出指定项目的有效文档、解析器、Chunk 类型和处理结果摘要，用于判断资料是否已经导入。",
      inputSchema: z.object({ project_id: z.string().uuid() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ project_id }) => {
      try {
        return toolResult(await apiClient.listProjectDocuments(project_id));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "search_knowledge_base",
    {
      title: "检索设计知识库",
      description: "按项目 ID 检索 Design_RAG，只返回可追溯文字证据和关联图片安全引用。",
      inputSchema: z.object({
        query: z.string().min(1).max(16000),
        project_id: z.string().uuid().optional(),
        evidence_top_k: z.number().int().min(1).max(50).default(8),
        include_images: z.boolean().default(true),
        image_mode: z.enum(["metadata", "thumbnail", "full"]).default("metadata"),
        filters: filterSchema,
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ query, project_id, evidence_top_k, include_images, image_mode, filters }) => {
      try {
        return toolResult(await apiClient.search({
          query,
          projectId: project_id,
          evidenceTopK: evidence_top_k,
          filters,
          includeImages: include_images,
          imageMode: image_mode,
        }));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "get_document_page",
    {
      title: "读取文档页面",
      description: "按项目、文档和原始页码读取页面图片及其关联图片资产。",
      inputSchema: z.object({ project_id: z.string().uuid(), document_id: z.string().uuid(), page_number: z.number().int().positive() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ project_id, document_id, page_number }) => {
      try {
        return toolResult(await apiClient.getDocumentPage(project_id, document_id, page_number));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "list_chunk_images",
    {
      title: "查询依据关联图片",
      description: "返回某条文字 chunk 直接关联、同页兜底关联的图片资产。",
      inputSchema: z.object({ project_id: z.string().uuid(), chunk_id: z.string().uuid() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ project_id, chunk_id }) => {
      try {
        return toolResult(await apiClient.listChunkImages(project_id, chunk_id));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "read_image_asset",
    {
      title: "读取知识库图片",
      description: "读取指定项目中的图片资产。默认返回安全 URL；请求 original 时在图片不超过 10MB 的情况下附带 MCP image content。",
      inputSchema: z.object({
        project_id: z.string().uuid(),
        asset_id: z.string().uuid(),
        variant: z.enum(["thumbnail", "preview", "original"]).default("thumbnail"),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ project_id, asset_id, variant }) => {
      try {
        const reference = imageReference(apiClient, project_id, asset_id, variant);
        if (variant !== "original") return toolResult(reference);
        const binary = await apiClient.readImageAsset(project_id, asset_id, variant);
        if (binary.data.byteLength > MAX_INLINE_IMAGE_BYTES) {
          return toolResult({ ...reference, inline: false, size_bytes: binary.data.byteLength, message: "图片超过 10MB，仅返回安全 URL。" });
        }
        const mimeType = binary.contentType.split(";", 1)[0] || "image/jpeg";
        return toolResult(
          { ...reference, inline: true, size_bytes: binary.data.byteLength, content_type: mimeType },
          [{ type: "image", data: binary.data.toString("base64"), mimeType }],
        );
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "search_materials",
    {
      title: "检索材料与家具材质",
      description: "检索材料表、物料、材料样板和视觉推测结果。视觉推测不等同于工程文档确认。",
      inputSchema: z.object({
        query: z.string().min(1).max(16000),
        project_id: z.string().uuid(),
        evidence_top_k: z.number().int().min(1).max(50).default(12),
        include_visual_inference: z.boolean().default(true),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ query, project_id, evidence_top_k, include_visual_inference }) => {
      try {
        const chunkTypes = ["material_item", "material_swatch", "table"];
        const contentOrigins = ["document_text", "ocr_extracted"];
        if (include_visual_inference) {
          chunkTypes.push("vision_surface", "furniture_material", "vision_analysis");
          contentOrigins.push("vision_generated");
        }
        return toolResult(await apiClient.search({
          query,
          projectId: project_id,
          evidenceTopK: evidence_top_k,
          filters: { chunk_types: chunkTypes, content_origins: contentOrigins },
          includeImages: true,
          imageMode: "metadata",
        }));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "get_retrieval_capabilities",
    {
      title: "查询检索能力",
      description: "返回支持的过滤器、材料 Chunk 类型、证据等级和引用字段。",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => {
      try {
        return toolResult(await apiClient.getCapabilities());
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool('run_asset_integrity_check', { title: '检查项目图片资产完整性', description: '检查项目图片资产的文件、哈希和溯源状态。', inputSchema: z.object({ project_id: z.string().uuid() }), annotations: { readOnlyHint: true } }, async ({ project_id }) => {
    try { return toolResult(await apiClient.createHarnessRun({ project_id, run_type: 'assets' })); } catch (error) { return toolError(error); }
  });
  server.registerTool('get_harness_run', { title: '读取 Harness 任务', description: '读取已创建的 Harness 评测任务。', inputSchema: z.object({ run_id: z.string().uuid() }), annotations: { readOnlyHint: true } }, async ({ run_id }) => {
    try { return toolResult(await apiClient.getHarnessRun(run_id)); } catch (error) { return toolError(error); }
  });
  server.registerTool('create_image_generation_job', { title: '创建图片生成任务', description: '创建待人工确认的项目图片生成任务。', inputSchema: z.object({ project_id: z.string().uuid(), prompt: z.string().min(1).max(16000), reference_asset_ids: z.array(z.string().uuid()).default([]), citation_ids: z.array(z.string()).default([]) }) }, async (payload) => {
    try { return toolResult(await apiClient.createImageGenerationJob(payload)); } catch (error) { return toolError(error); }
  });
  server.registerTool('get_image_generation_job', { title: '读取图片生成任务', description: '读取图片生成任务状态和溯源信息。', inputSchema: z.object({ job_id: z.string().uuid() }), annotations: { readOnlyHint: true } }, async ({ job_id }) => {
    try { return toolResult(await apiClient.getImageGenerationJob(job_id)); } catch (error) { return toolError(error); }
  });
  server.registerTool('confirm_image_generation_job', { title: '确认图片生成任务', description: '明确确认后将任务交给生成适配器。', inputSchema: z.object({ job_id: z.string().uuid() }) }, async ({ job_id }) => {
    try { return toolResult(await apiClient.confirmImageGenerationJob(job_id)); } catch (error) { return toolError(error); }
  });
  server.registerTool('run_rag_test', { title: '运行 RAG 检索测试', inputSchema: z.object({ project_id: z.string().uuid() }) }, async ({ project_id }) => toolResult(await apiClient.createHarnessRun({ project_id, run_type: 'rag' })));
  server.registerTool('run_mcp_contract_test', { title: '运行 MCP 契约测试', inputSchema: z.object({ project_id: z.string().uuid() }) }, async ({ project_id }) => toolResult(await apiClient.createHarnessRun({ project_id, run_type: 'mcp' })));
  server.registerTool('run_ragas_evaluation', { title: '运行 RAGAS 评测', inputSchema: z.object({ project_id: z.string().uuid() }) }, async ({ project_id }) => toolResult(await apiClient.createHarnessRun({ project_id, run_type: 'ragas' })));
  server.registerTool('get_harness_report', { title: '读取 Harness 报告', inputSchema: z.object({ run_id: z.string().uuid() }), annotations: { readOnlyHint: true } }, async ({ run_id }) => toolResult(await apiClient.getHarnessRun(run_id)));
  server.registerTool('recommend_reference_images', { title: '推荐方案参考图片', inputSchema: z.object({ project_id: z.string().uuid(), query: z.string().min(1) }), annotations: { readOnlyHint: true } }, async ({ project_id, query }) => toolResult(await apiClient.search({ query, projectId: project_id, evidenceTopK: 8, includeImages: true, imageMode: 'thumbnail' })));
  server.registerTool('preview_image_generation_prompt', { title: '预览图片生成提示词', inputSchema: z.object({ project_id: z.string().uuid(), prompt: z.string().min(1) }), annotations: { readOnlyHint: true } }, async ({ project_id, prompt }) => toolResult({ project_id, prompt, provider: 'chatgpt_browser', status: 'awaiting_confirmation', asset_type: 'ai_concept_image' }));
  return server;
}

export function createHttpServer(options = {}) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname === "/health") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ service: "design-rag-mcp", status: "ok", transport: "streamable-http" }));
      return;
    }
    if (url.pathname !== "/mcp") {
      res.writeHead(404, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ detail: "Not found" }));
      return;
    }

    const mcpServer = createDesignRagMcpServer(options);
    const transport = new NodeStreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => {
      transport.close().catch(() => undefined);
      mcpServer.close().catch(() => undefined);
    });
    try {
      await mcpServer.connect(transport);
      await transport.handleRequest(req, res);
    } catch (error) {
      if (!res.headersSent) {
        res.writeHead(500, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ detail: error instanceof Error ? error.message : String(error) }));
      }
    }
  });
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("server.js")) {
  createHttpServer().listen(PORT, HOST, () => {
    console.log(`Design_RAG MCP listening on http://${HOST}:${PORT}/mcp`);
  });
}
