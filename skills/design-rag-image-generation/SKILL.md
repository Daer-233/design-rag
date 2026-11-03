---
name: design-rag-image-generation
description: 根据 Design_RAG 项目依据和人工选择的参考图片创建可追溯的 AI 方案图生成任务。
---
# Design_RAG Image Generation
- 先按项目 ID检索公司风格、材料和空间依据。
- 推荐同一项目的参考图片，由用户最终勾选。
- 创建任务时保存 prompt、prompt_version、reference_asset_ids 和 citation_ids。
- 任务初始为 `awaiting_confirmation`；只有用户明确确认后才调用 `confirm_image_generation_job`。
- 当前生成适配器为 ChatGPT 浏览器流程；网页登录状态不写入知识库。
- 结果类型固定为 `ai_concept_image`，必须保留 SHA-256、项目、参考图、提示词和依据关系。
- 生成图只能作为方案参考，不得冒充原始项目图片或材料事实。