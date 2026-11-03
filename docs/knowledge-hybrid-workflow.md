# 工作知识助手：混合检索与 LangBot Codex 回答

更新：2026-10-29。用户已确认采用“向量＋稀疏检索＋标题关键词共同召回，统一重排”，允许跨资料综合，并逐条引用来源；不同适用版本和冲突规定分别处理。

## 当前实际链路

```text
上传文档 / 整理图文条目
  → 本地提取或明确提交的 MinerU 解析
  → 父子块、原文页码、逐步配图归档
  → 发布后排队：Embedding → 独立 Qdrant collection

用户在 LangBot 提问
  → 既有 Codex 处理器调用“工作知识”库
  → 问题 Embedding → Dense＋Sparse 召回
  → 合并标题 / 原文关键词候选 → 去重与候选融合
  → 统一 Rerank → 相关证据锚点
  → 补齐各自的父块上下文、配图、文件与原文页码
  → 经 HTTP RAG Connector 返回各来源证据包
  → 同一个 LangBot Codex 处理器综合回答
```

这里的 Codex 明确指 LangBot 中已有的 **Codex** 处理器（`46b1174d-a808-46e3-ace9-ce34432803db`，CodexAgent），不是当前开发对话，也不是知识后端的另一个回答模型。工作知识库 ID 为 `b557ed18-fd7a-408c-89b7-fc9ffcc3e446`，现有绑定、用户侧 Daemon 与本地上下文修订保持不变。

网页 <http://127.0.0.1:8060/knowledge/> 用于资料管理和检索证据预览。网页查询固定 `source_only=true`，不再显示其他回答模型的摘要开关。最终问答仍在 LangBot 的 Codex 处理器中完成。

## 检索与来源规则

- 不再因标题或关键词匹配较强而跳过向量与重排。三个通道各最多召回 40 个候选，融合去重后，初始重排预算仍为 **8**。先给其他文档保留候选位置，再填充剩余额度。
- 最多保留配置的 8 个证据锚点，每份资料默认最多 2 个；调用方 `top_k` 可以进一步缩小。低于 `KNOWLEDGE_RERANK_MIN_SCORE=0` 的原始 BGE 重排分数不作为证据。此分数不是概率，也未完成真实业务语料校准。
- 仅允许已发布、内容就绪的当前有效记录；向量命中再次核对 PostgreSQL 中的证据 ID、文档版本和资料归属。草稿与历史版本不进入默认查询。
- 同一系列 / 适用对象存在多个适用版本时，先返回版本选择，不把多个版本混入同一次答案。不同资料可综合，但保留各自标题、适用对象、版本和来源；有冲突时交给 Codex 分别说明，不擅自裁决。
- 每个命中锚点回填其所在的 20 段父块上下文，不再固定只返回文档开头。命中和上下文分别标记 `is_match`；图片继续按原有步骤 / 章节关联保留。
- 原文上下文总预算为 24,000 字符。完整段落不截断；预算导致未展开的上下文或锚点有计数与明确提示，可逐资料查看前文、后文或原文件。检索批次用 `retrieval_page` 表示，不能作为原文件页码。
- 向量服务故障时，可从关键词召回并尝试重排，模式为 `keyword_rerank`；重排故障时明确标记 `keyword_fallback`，不将未经重排的向量结果伪装为有效证据。无证据时不生成替代做法。检索状态提示 `retrieval_notice` 不是知识证据。

## 接口和启动

`POST /api/v1/manuals/ask` 保留原字段，新增 `sources`：每个来源包括 `entry`、`steps`、`matched_evidence_ids`、上下文完整度和独立分页。顶层 `steps` 保留为兼容的扁平列表；新增 `retrieval_metrics` 记录三路数量、重排输入数、返回证据数与各阶段耗时。

LangBot 的 `POST /api/v1/manuals/retrieve` 将来源分别转换成证据包，保留图片元数据和引用。原 HTTP RAG 连接配置继续可用。已有只读 `@knowledge` 查询信封用于版本选择、前文和后文。

```powershell
# 同时启动缺失的共享模型；8031 / 8032 已存在时验证身份并复用
powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-manuals.ps1 -WithModels

# LangBot Codex 用户侧连接
powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-langbot-codex.ps1
```

模型由既有 Conda Python 承载，服务端口为 Embedding `8031`、Rerank `8032`，仍使用原有本地 BGE 权重，不下载和重复加载。新启动实例默认限制 `OMP_NUM_THREADS` / `MKL_NUM_THREADS` 为 8，可通过 `start-knowledge-models.ps1 -CpuThreads` 调整；复用已有实例时不改变其配置。首次加载较慢，本轮冷启动并行预热各约 87–91 秒。

发布自动进入持久化索引队列；检索与索引使用同一单飞锁，忙时索引等待，不并发抢占 CPU。重启时重试被中断的索引，以及已发布且待索引 / 失败的记录。停止知识服务不停止共享模型和数据库。原设计 RAG 后端没有因此启动或修改连接配置。

## 验收与后续交接

- 知识后端回归 41 项通过，覆盖标题强匹配也执行全链路、去重 / 预算、跨资料图片引用、版本阻断、冲突原文保留、过期向量排除、重排失败降级、长资料后段命中和索引队列等待。
- 既有 Codex Daemon 上下文 / 权限撤销回归 2 项通过，共 43 项。
- 两份现有验收资料已建立向量索引。三道实际模型查询均为 `hybrid_rerank`，重排输入 8；跨资料问题返回两份来源，财务问题因无证据而不回答金额或人员。样例检索耗时约 **15–19 秒**，并非生产性能基准。
- 经 LangBot 工作知识库实际返回两个来源包；现有 LangBot Codex 处理器实际完成跨资料综合，约 **47 秒**（包含其工具调用和生成），引用了两份资料并正确区分原文件页码缺失。
- 验收记录、接口响应和页面截图位于 `artifacts/knowledge-hybrid-20261029/`，以最终 `verification.json` 为准。可用 `scripts/check-knowledge-hybrid.py` 复验本地检索；`scripts/check-knowledge-ui.cjs --hybrid` 验证部署页面的多来源展示与四种宽度。

目前仍只有两份自编验收示例。下一步应导入真实 IT、入职、财务和运营资料，建立经核实的问题集后再校准相关性阈值和预算；不能把当前通过的少量样例当成业务准确率。图片元数据、来源关联及本机缩略图已验证，聊天平台原图投递仍未验收。外部 OCR / 视觉提交仍沿用明确确认流程。

新对话先阅读本文件、`docs/langbot-work-knowledge.md` 与最终验收 JSON。沿用既有 LangBot Codex 处理器，不另建回答模型；保留 `manual_rag`、`manual_rag_chunks_v1` 和 `data/manuals`，不接入原设计库的旧 Agent 字节码。
