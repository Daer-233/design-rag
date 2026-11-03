# MCP 图片检索合同

仅通过 `design-rag` MCP 使用下列只读工具。`project_id` 必须来自 `list_projects` 或 `resolve_project` 的实际返回值。

## `search_knowledge_base`

```json
{
  "query": "客厅电视背景墙、墙面和主要材料",
  "project_id": "<UUID>",
  "evidence_top_k": 6,
  "include_images": true,
  "image_mode": "metadata"
}
```

- `metadata`：默认。返回文本依据、去重后的图片元数据、缩略图/预览 URL 与 `resource_uri`。
- `thumbnail`：适合需要引导用户查看图片，但无需读取原图的场景。
- `full`：仅声明可读取原图；不要假设检索响应直接携带 Base64 大图。
- 结果内的 `images` 是整次检索去重后的图片；每条 `results[].images` 表示与该 chunk 的关联图。
- 图片关联先后为：chunk 直接关联、父 chunk、同版本同页、同一图像文档的安全回退。不能跨项目。

## `search_materials`

材料问题优先使用。它默认同时检索文档/OCR材料证据和视觉结果，且返回关联图片。

- 用户只接受材料书、表格或标注事实时设 `include_visual_inference: false`。
- 启用视觉结果时，必须将其标为视觉观察或视觉推断，不得提升为材料书确认。

## `list_chunk_images`

```json
{"project_id": "<UUID>", "chunk_id": "<UUID>"}
```

当 `search_knowledge_base` 命中一条文字依据但未带图、或需要查看更多相关图时调用。仅展示与该 chunk 确实关联的资产。

## `get_document_page`

```json
{"project_id": "<UUID>", "document_id": "<UUID>", "page_number": 18}
```

用于用户要求核验某一 PDF/PPT 页，或检索结果已有可靠页码时。不要用页码猜测文件。

## `read_image_asset`

```json
{
  "project_id": "<UUID>",
  "asset_id": "<UUID>",
  "variant": "thumbnail"
}
```

- `thumbnail` / `preview`：只返回安全引用，优先使用。
- `original`：在图片不超过 10MB、且 MCP 客户端支持图片内容时可返回 image content；更大文件仅返回 URL 与大小提示。
- 不批量读取原图。读取失败不影响文字结论；如实说明图片不可用。

## 呈现规则

1. 文字结论不可依赖图片 URL 才能被理解。
2. 每张图片必须带文件名、页码（如有）、图片类型、关联对象和证据等级。
3. 相同 `asset_id` 或相同文件/页码/类型去重。
4. `thumbnail_url`、`preview_url`、`original_url` 与 `resource_uri` 都是受控引用；不转换为磁盘路径，不去除其项目访问限制。
5. 不将图片引用传播到不属于当前项目的回答或外部服务。