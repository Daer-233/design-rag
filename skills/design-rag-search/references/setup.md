# 为其他 Agent 配置 Design_RAG MCP + Skill

## 架构

```text
其他 Agent + design-rag-search Skill
              |
              | Streamable HTTP MCP（只读）
              v
http://<知识库主机局域网 IP>:8040/mcp
              |
              | MCP Server 持有只读 Agent Token
              v
Design_RAG 后端 http://127.0.0.1:8030
```

调用 Skill 的 Agent 不应保存、携带或看到 `DESIGN_RAG_API_TOKEN`。Token 只配置在运行 MCP Server 的知识库主机上。

## 启动知识库主机上的 MCP Server

确认根目录 `.env` 包含：

```dotenv
DESIGN_RAG_API_BASE_URL=http://127.0.0.1:8030
DESIGN_RAG_API_TOKEN=<只读 Agent Token>
DESIGN_RAG_MCP_HOST=0.0.0.0
DESIGN_RAG_MCP_PORT=8040
```

然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\run-mcp-server.ps1
```

健康检查地址是 `http://<知识库主机局域网 IP>:8040/health`，MCP 地址是：

```text
http://<知识库主机局域网 IP>:8040/mcp
```

`0.0.0.0` 只用于服务端监听；客户端配置必须使用主机实际局域网 IP，不能使用 `0.0.0.0`。

## 配置其他 Agent

1. 在目标 Agent 的 MCP 设置中新增一个 Streamable HTTP Server，名称建议为 `design-rag`。
2. URL 填写 `http://<知识库主机局域网 IP>:8040/mcp`。
3. 不要在客户端 MCP 配置中添加知识库 Bearer Token；认证由 MCP Server 到 Design_RAG 后端的内部调用完成。
4. 将整个 `design-rag-search` 文件夹安装到目标 Agent 的 Skills 目录，或导入 `design-rag-search.zip`。
5. 连接后先确认工具列表至少包含：
   - `list_projects`
   - `resolve_project`
   - `list_project_documents`
   - `search_knowledge_base`
   - `search_materials`
   - `get_retrieval_capabilities`
6. 用 `list_projects` 做首次连通性测试，再选一个返回的项目 ID 调用 `list_project_documents`。

当前 Token 已配置为 `all_projects=true` 时，`list_projects` 会覆盖当前和未来新增且已有有效知识文档的项目，不需要为每次新导入重新发 Token。

## 排障

- 无法连接：检查 8040 端口、主机局域网 IP、MCP 进程和 `/health`。
- `DESIGN_RAG_API_TOKEN is not configured`：只修复知识库主机上的 MCP Server 环境，不把 Token 发给调用 Agent。
- 项目为空：确认后端正在运行、项目已有 active 文档、Token 仍为全项目只读范围。
- 检索为空：先调用 `list_project_documents` 查看是否有 `material_item`、`vision_surface`、`furniture_material` 等 Chunk。
- 材料只有视觉推测：检查 Admin 导入时是否显式授权 Vision，以及同项目是否同时导入材料表。