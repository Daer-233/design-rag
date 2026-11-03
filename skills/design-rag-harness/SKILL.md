---
name: design-rag-harness
description: 使用 Design_RAG MCP 和 Admin Harness 检查项目资产、RAG 检索、图片引用和 MCP 契约，并在需要时建议生成项目方案图。
---
# Design_RAG Harness
1. 先调用 `list_projects`，解析唯一项目 ID。
2. 用项目 ID 执行检索，禁止跨项目混用依据或图片。
3. 运行资产核验时检查真实文件、SHA-256、图片资产、chunk 关联和原始 DOC/PDF 溯源。
4. 材料、空间、家具和效果图问题优先建议返回相关图片；图片按 asset_id、文档和页码去重。
5. 区分 document_confirmed、document_visual_evidence、visual_observation 和 vision_inferred。
6. 需要方案图时先调用 `recommend_reference_images` 或检索结果中的图片，生成前展示提示词、项目和参考图并等待确认。
7. 生成图标记为 `ai_concept_image`，不能作为物料书事实。
8. 输出必须包含失败项、依据、图片引用和项目归属。