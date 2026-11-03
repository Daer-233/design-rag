# Design_RAG MCP Server

Node.js 只读 MCP 网关。它把 Design_RAG 已认证的项目目录与检索 API 暴露为 MCP 工具，供 Chat Agent 和安装了 `design-rag-search` Skill 的其他 Agent 使用。

## 架构与安全边界

```text
Agent / Chat -> MCP (8040) -> Design_RAG API (8030) -> knowledge base
```

- MCP 工具全部为只读。
- 后端 Agent Token 只由 MCP Server 进程读取，不能写入 Skill、客户端配置、日志或 Git。
- 项目可见范围由后端 Token 决定；`all_projects=true` 会自动覆盖今后新增且已导入有效文档的项目。
- MCP 不提供 Admin、导入、审批、删除或数据库直连能力。

## 环境变量

```dotenv
DESIGN_RAG_API_BASE_URL=http://127.0.0.1:8030
DESIGN_RAG_API_TOKEN=<read-only Agent Token>
DESIGN_RAG_MCP_HOST=0.0.0.0
DESIGN_RAG_MCP_PORT=8040
```

## 推荐启动方式

从项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\run-mcp-server.ps1
```

脚本会安全读取根目录 `.env`，优先使用系统 Node.js，否则使用 Codex bundled Node.js；不会打印 Token。

也可以在已配置环境变量和 Node.js 20+ 的情况下直接运行：

```powershell
cd I:\Design_RAG\apps\mcp-server
node src/server.js
```

## 地址

- 健康检查：`GET http://<知识库主机局域网 IP>:8040/health`
- MCP Streamable HTTP：`http://<知识库主机局域网 IP>:8040/mcp`

服务端监听可以使用 `0.0.0.0`；其他电脑的 MCP 客户端必须填写主机实际局域网 IP。

## 工具

- `list_projects`：查询当前 Token 可访问且已有有效知识文档的项目。
- `resolve_project`：用名称、external key 或 UUID 解析项目，不在多个候选中自动选择。
- `list_project_documents`：查询项目文档、解析器、Chunk 类型和内容来源。
- `search_knowledge_base`：按项目和可选过滤器检索通用知识证据。
- `search_materials`：检索材料表、材料样板、家具材质和可选视觉推测。
- `get_retrieval_capabilities`：查询支持的过滤器、Chunk、内容来源、证据等级和 citation 字段。

材料结果必须区分：

- `document_confirmed`
- `vision_inferred`
- `candidate_match`
- `unknown`

## 测试

```powershell
node --test --test-isolation=none
```

HTTP 集成测试覆盖 MCP `initialize`、`tools/list`、`list_projects` 和 `search_knowledge_base`。