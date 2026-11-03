# 第一阶段检索评测与配置说明

日期：2026-10-28。工作区：`I:\Design_RAG`。

## 已实现的变化

- 新增 `RETRIEVAL_RERANK_CANDIDATE_TOP_K`，表示送入重排模型的候选预算。
- `RETRIEVAL_EVIDENCE_TOP_K` 继续表示最终证据目标，请求中的 `evidence_top_k` 只调整此目标。
- 新名称优先；没有新名称时兼容旧 `RETRIEVAL_RERANK_TOP_K`；两者都没有时默认 8。
- 本地 `.env` 已新增候选预算 8，保留旧值 6；当前新加载 Settings 的有效值为 8，证据目标为 8。运行中的旧进程需重启后加载；本轮不重启业务后端。
- 召回 100、粗排 40、每文档最多 2 条证据的现有规则保留。因 CPU 实测成本，初始默认值从试验中的 20 收敛为 8；20/40 保留为可选评测档位。8 不是已证明最优值，真实耗时尚未完成。
- `pipeline` 新增候选预算、实际重排输入数、粗排数、最终证据数与缺口；旧响应键 `rerank_top_k` 保留，其值为有效输入预算。

原问题：默认 6 条候选在交给重排前已被截取，无法返回目标 8 条，也无法让粗排第 7 条以后的候选被重排纠正。显式配置输入 6 仍被允许，用于兼容或成本控制，此时缺口会如实返回。

## 基线与环境

- 修改前相关离线测试：25 passed，2 warnings。
- 变更前实际 `.env`：召回 100、粗排 40、旧重排预算 6、证据目标 8、每文档 2；本地回答开关为 true。本轮未改回答开关。
- PostgreSQL 只读检查：6 个项目、3072 个块、147 个文档版本、1 个评测套件、30 道评测题。
- 导出套件对应绿城下沙项目，指纹覆盖 2134 个块和 202 个图片资产；具体 SHA-256 见题集 JSON。导出过程未改数据库。
- 题集：[stage1-suite-v1.json](../test-data/rag-evaluation/stage1-suite-v1.json)。30 题全部 `needs_review`；参考答案未经人工核实，部分属于评分说明，不能当作事实标准答案。
- 项目 `.venv` 指向缺失的 uv Python 3.11。已有可用环境为 `C:\Users\liwj\.conda\envs\design-rag\python.exe`（3.11.15）；本阶段使用它验证，不重建环境。
- 启动时本地 Embedding/Rerank 服务不可达。没有因此把离线夹具测试解释成真实模型准确率。真实采集状态以本次结果记录和交接文件为准。
- 基线快照和非敏感配置摘要保存在 `tmp/rag-stage1-20261028/`；工作区原有大量未提交变更，本轮没有重置或提交它们。

## 题集标注

在人工检查原始来源后填写：

```json
{
  "annotation_status": "reviewed",
  "category": "exact_fact",
  "reference_answer": "经原文件核实的事实答案",
  "relevant_chunk_ids": ["真实块 UUID"],
  "relevant_sources": [{"document_id": "真实文档 UUID", "page_start": 3, "page_end": 3}],
  "relevant_asset_ids": ["真实图片资产 UUID"]
}
```

`null` 表示尚未标注，不计质量分数；`relevant_chunk_ids: []` 只用于已经确认当前授权语料应无结果的负例。图片、来源列表须按各自评测目标完整标注，不能把漏标的正确结果误判为错误。未知有效期、方案分支等问题需要明确查询范围。候选参考答案不可直接批量改成 reviewed。

## 运行命令

从 `I:\Design_RAG` 的 PowerShell 执行：

```powershell
$ragPython = 'C:\Users\liwj\.conda\envs\design-rag\python.exe'

# 可选：导出已有套件为新的版本；不要覆盖已标注的题集。
& $ragPython scripts/evaluate-retrieval.py export --suite-id fe041649-7a29-4ca9-95dc-ea2ec999fe73 --output test-data/rag-evaluation/stage1-suite-v2.json

# 使用本地 PG/Qdrant/Embedding/Rerank，先做最多 3 题的烟雾对比。
& $ragPython scripts/evaluate-retrieval.py capture --suite test-data/rag-evaluation/stage1-suite-v1.json --budgets 6 20 40 --k 8 --max-cases 3 --output tmp/rag-stage1-20261028/live-captures.json

# 对匹配版本的成功采集结果离线计分，无外部模型调用。
& $ragPython scripts/evaluate-retrieval.py score --suite test-data/rag-evaluation/stage1-suite-v1.json --captures tmp/rag-stage1-20261028/live-captures.json --output tmp/rag-stage1-20261028/live-scores.json
```

先标注、再采集；题集在采集后变动会导致指纹不一致。为已有采集补标时，应另存版本并重新采集。语料变化也需重新核实标注和指纹，不能拼接不同语料或题集的结果。

采集默认使用题集 `principal_roles: []`，仅访问该项目下对应角色可见的证据。需要评测角色时显式填写角色并另存题集版本；不要为了“命中”关闭权限校验。

## 指标含义与局限

- `recall_at_k`：人工标注相关块被最终 Top-K 命中的比例。
- `evidence_precision_at_k`：去重后证据块中，人工标注相关块所占比例。
- `reciprocal_rank`：首个相关块的倒数排名。
- `citation_source_precision`：返回证据引用是否落在标注文档/页码范围；不等价于回答每个主张都被来源支持。
- `image_precision` / `image_recall`：根据标注资产 ID 检查图片相关性；没有完整图片标注不计分。
- `empty_result_correctness`：经审核的无证据负例是否返回空证据；不是 LLM 拒答评分。
- 汇总同时报告每项实际计分题数、平均/中位耗时、证据数和模型调用数。未标注题只有运行统计，质量分数缺失而非 0 分。

采集使用既有 SearchService，检查语料前后指纹，记录模型版本/可用权重哈希、代码哈希、题集哈希及配置。只保存块 ID、必要引用定位、图片资产 ID 和流水线统计，不保存图片签名链接、原始私密路径或证据全文。模型预热单独记录；只做一轮、固定预算顺序的小样本耗时仍受缓存和系统负载影响，不代表性能基准。

现有 RAGAS 路径继续负责忠实度和基于答案的上下文评测。其外部调用沿用现有明确提交机制；本阶段新增命令不调用答案模型或 RAGAS，也不自动把导出题集写回数据库。

## 后续完成条件

P02 的真实质量评测还需要：人工核实题目与参考答案、补齐正确证据/图片标注、按同一语料和模型完成 6/8/20/40 对比；根据结果决定是否扩大默认预算。工具与离线回归完成不代表这些工作已经完成。


## 本次真实试跑记录

本次临时启动已有本地模型，未下载权重。CPU 成本较高，三题试跑收敛为一题，随后主动停止大预算试验。临时模型进程已停止，未重启业务后端。

| 记录 | 配置预算 / 实际输入 | 最终证据 / 目标 | 总耗时 | 重排耗时 |
| --- | --- | --- | --- | --- |
| 首次首题 | 6 / 6 | 6 / 8 | 59.591 秒 | 55.746 秒 |
| 缩减试跑首题 | 6 / 6 | 6 / 8 | 59.373 秒 | 57.014 秒 |

这些是实际服务烟雾记录，不是准确率成绩或稳定性能基准。预算 8/20/40 没有完成的可比较结果。未完成采集已标记 interrupted，离线计分会拒绝这些记录。配置召回 100 时融合后实际出现 174 个候选，粗排为 40，故配置预算不能等同于实际计数。

模型身份、权重指纹、代码及题集哈希、实际数量与调用统计见 tmp/rag-stage1-20261028/live-captures.json 和 one-case-captures.json。每条已完成查询调用本地 Embedding/Rerank 各一次，外部 LLM 为 0。30 题仍需人工标注，没有计算客观质量分数。


### 本次验证结果

变更前相关组 25 passed；扩展相关组 50 passed（2 条既有弃用警告）；最终预算及离线评测模块 22 passed。详细记录：artifacts/rag-stage1-20261028/verification.json。原有 test_retrieval_ranking.py 与修改前快照一致。
