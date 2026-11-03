# Design_RAG Agent 检索 API（本机受控环境）

## 边界

Agent 只可调用证据检索接口，不能直接访问 PostgreSQL、Qdrant、文件系统或管理接口。系统不会在响应中返回绝对本地路径、源文件路径、图片存储位置、数据库连接信息或密钥。

- 地址：`http://127.0.0.1:8030/api/v1/search`
- 方法：`POST`
- 认证：`Authorization: Bearer <Agent Token>`
- 权限：服务端按 Token 固定 Agent 身份、角色与项目范围；请求正文无法扩大权限。
- 回答接口：`/api/v1/answer` 在当前 Agent 检索模式中已禁用，返回 `403`，不会调用 GPT。

## 请求

```json
{
  "query": "客厅地面使用什么石材，型号和规格是什么？",
  "project_id": "<允许访问的项目 UUID>",
  "evidence_top_k": 8,
  "filters": {
    "file_types": ["pdf", "docx"],
    "chunk_types": ["material_item", "material_swatch", "table", "image_ocr"],
    "content_origins": ["document_text", "ocr_extracted"],
    "page_start": 1,
    "page_end": 30
  }
}
```

所有筛选项都是可选的。事实型问题应优先查询 `material_item`、`material_swatch`、`table`、`image_table` 和 `image_ocr`；风格与氛围问题可再包含 `image_caption`、`image_structure` 或 `vision_analysis`。

## 安全响应

每条证据只包含项目名、文档名、页码、标题/章节、Chunk 类型、内容来源、证据文本、分数、置信度和安全来源定位符。`vision_generated` 内容会明确标示为模型生成，不能视为型号、品牌、规格等原始事实。

## Token 配置

当前本地兼容 Token 已收紧为仅可访问已入库的“04 杭州中海”项目。新增项目后，必须由维护人员把项目 UUID 显式添加到 `.env` 的 `DESIGN_RAG_API_ALLOWED_PROJECT_IDS`，然后重启 Backend。

需要多 Agent / 多项目隔离时，改用 `.env` 的 `DESIGN_RAG_AGENT_TOKENS`（JSON 对象）。令牌、项目范围和角色仅保存在服务端，示例中的占位值必须替换：

```dotenv
DESIGN_RAG_AGENT_TOKENS={"<token-1>":{"agent_id":"design-agent-a","project_ids":["<project-uuid>"],"roles":["designer"]}}
```

不要把 Token、数据库密码或项目文件路径写入 Agent 提示词、日志或检索响应。
