# Design_RAG 改进交接说明

更新时间：2026-10-28（Asia/Shanghai）  
主工作区：`I:\Design_RAG`  
实施计划：[rag-upgrade-plan-20261028.md](rag-upgrade-plan-20261028.md)

## 1. 用户目标及授权范围

用户要求分析 HKUDS/RAG-Anything、HKUDS/LightRAG、getzep/graphiti、johnson7788/graphmind 能否改善现有 RAG，随后要求：“先弄一个计划表和交接文件，后面切换新对话就可以继续了”。

用户随后明确要求“先开始第一阶段”。P00/P01 已实施，P02 工具已建立；下一步补真实题目证据标注和有预算的实测，不重复从计划整理开始。完整准确率评测尚未完成。

## 2. 已形成的技术方向

保留 PostgreSQL + Qdrant，不整体迁移。优先改善现有检索与图文证据链；按实际需求试验关系检索或时序记忆。不要同时部署四套框架并对全部资料重复抽取。

- RAG-Anything：借鉴图片结合章节/图注/邻近文本，以及查询时视觉取证。
- LightRAG：候选的跨文档关系检索引擎。
- Graphiti：候选的时序事实/项目决策/偏好记忆引擎；也要与直接使用 PG 的方案比较。
- GraphMind：图谱可视化与交互参考；当前代码基于 LightRAG + RAG-Anything，不是独立第四种算法。

这些是架构建议，没有同语料实测数据证明任何框架更准确。

## 3. 本轮代码检查确认的现状

以下代码在前一轮已读取；实施前重新检查，防止文件被其他工作修改。路径相对于 `I:\Design_RAG`。

| 代码位置 | 已确认事项 / 下一步影响 |
| --- | --- |
| `apps/backend/app/services/rag_service.py` | Dense/Sparse 混合召回、父子块、权限前置过滤、PG 回查、粗排、重排、文档多样性和图片关联已实现 |
| `apps/backend/app/core/config.py` | 现默认召回 100、粗排 40、候选预算默认 8、证据目标 8；本地 .env 新键=8，旧键=6，新键优先。已核验有效值 |
| `apps/backend/app/services/answer_service.py` | 已有基于证据回答；现有证据包主要由子块/父块文字组成，不等于查询时将原图发送给模型 |
| `apps/backend/app/providers/llm/openai_compatible_answer.py` | 已有约束引用 ID 和证据不足行为的文本回答 Provider |
| `apps/backend/app/services/mineru_result_normalizer.py` | 优先结构化 JSON；保留页码、bbox、表格与图片关联和邻近文字；Markdown 为兼容回退 |
| `apps/backend/app/services/visual_analysis_service.py` | 已有视觉分析任务及派生块；区分 vision_inferred、candidate_match 等 |
| `apps/backend/app/providers/llm/openai_compatible.py` | analyze_image 输入主要为图片、哈希及通用指令；未见章节/图注/邻近段落作为显式视觉上下文参数 |
| `apps/backend/app/services/rag_evaluation_service.py` | 已有 RAGAS 持久化评测流程：faithfulness、context_precision、context_recall；应复用后扩展 |
| `apps/backend/app/providers/vector/base.py` | VectorProvider 面向预计算向量和块候选；关系/记忆检索不适合直接塞入此接口 |
| `apps/backend/app/services/agent_workflow.py` | 当前通过 SourcelessFileLoader 加载 `_agent_workflow_legacy.cpython-311.pyc`；未完整审阅内部工作流，不能断言 Agent 完全没有查询重构 |
| `apps/backend/app/services/agent_service.py` | 存在 rewritten_query 的调用/记录路径；SearchService 本身返回 raw=effective，不能据此推断整个 Agent 不支持重构 |

README 中“最终回答尚未接入”等状态落后于工作区代码，不可仅靠 README 判断现状。本轮主分析对象是 `I:\Design_RAG`；只看过 `I:\Design_RAGv2` 根目录，未对其实现进行同等审查，不要误在 v2 实施。

## 4. 第一项可执行任务：P00 + P01

1. 阅读适用的 AGENTS.md，查看 Git/工作区状态，保留其他未提交修改。
2. 检查实际配置与既有测试组织；不要输出 .env、令牌或密钥。只记录相关非敏感配置值及其来源。
3. 核实 SearchService 在调用 reranker 之前的候选截取逻辑；当前源码使用 retrieval_rerank_top_k 先缩减输入。
4. 将“重排输入预算”和“最终证据预算”定义清楚，保持配置兼容或给出明确迁移说明；选择默认值要考虑 CPU 延迟。
5. 针对性检查候选数、文档多样性、调用失败和权限链路；测试应验证行为，不重复实现细节。
6. 使用现有评测入口比较输入 6 / 20 / 40；仅在服务、语料及调用授权均就绪时运行真实评测。否则先完成离线回归并记录未实测项，不杜撰指标。
7. 更新计划表、交接文件及确实受影响的说明；报告修改、验证结果和遗留事项。

可先查看：`apps/backend/tests/test_retrieval_ranking.py`、`test_access_policy.py`、`test_answer_service.py`、`test_rag_evaluation_service.py`。测试路径存在不表示本轮运行或通过；根据仓库配置选择实际运行命令。

## 5. 后续设计约束

- 记忆分为资料事实、项目决策、用户偏好，不互相冒充。
- 决策记录涵盖有效时间、记录时间、状态、适用范围、来源及替代关系；“后录入”不等于“更晚生效”或“全项目替代”。
- 项目/空间/方案隔离先于实体合并；实体别名不能导致跨项目资料或权限泄漏。
- 原文更新、删除、撤权必须传播至向量、摘要、关系和记忆；节点摘要本身也可能包含受限信息。
- PG 权威记录与来源可重建派生索引；图谱返回结果仍需权威回查。
- 新框架模型接口需适配；现有自定义 Embedding/Rerank HTTP 接口不自动等同 OpenAI 协议，向量维度一致也不代表语义空间兼容。
- Graphiti 需要额外图存储与服务运维；具体后端和版本在 PoC 前核验，不假设可直接复用当前 PG/Qdrant 存储。
- 接入 Agent 查询路由/记忆前先解决可读工作流源码问题；恢复过程中不得擅自删除旧字节码或凭猜测重写全部行为。

## 6. 外部证据与版本注意事项

查阅日期：2026-10-28；分支内容会变化，接入时固定版本并重新核验。

- RAG-Anything：https://github.com/HKUDS/RAG-Anything
- 上下文感知处理：https://github.com/HKUDS/RAG-Anything/blob/main/docs/context_aware_processing.md
- 视觉增强查询：https://github.com/HKUDS/RAG-Anything/blob/main/raganything/query.py
- LightRAG：https://github.com/HKUDS/LightRAG
- 查询模式：https://github.com/HKUDS/LightRAG/blob/main/lightrag/base.py
- Graphiti：https://github.com/getzep/graphiti
- 时序关系字段：https://github.com/getzep/graphiti/blob/main/graphiti_core/edges.py
- GraphMind：https://github.com/johnson7788/graphmind
- 实际引擎与兼容补丁：https://github.com/johnson7788/graphmind/blob/main/backend/app/services/rag_engine.py

重要发现：LightRAG 当前主分支已集成 RAG-Anything 多模态能力。GraphMind 仓库简介的“微软 GraphRAG”与当前引擎不一致；实际引擎说明已替换旧 Microsoft GraphRAG CLI，并有特定版本兼容补丁。不要复制历史补丁作为长期集成方式。

## 7. 既有相关文档与工具情况

- `docs/openviking-integration-assessment-20261013.md`：此前另一份方案评估，可参考但需重新核验。它记录的长会话窗口问题本轮未重新检查，不能标记为当前已复现。
- 本轮普通 exec 与 Node REPL 曾因 Windows sandbox 的 `helper_unknown_error: setup refresh had errors` 启动失败；沙箱外 exec 可读取项目。此为工具环境问题，不代表项目代码报错；下一对话先尝试正常工具，不必预先要求用户重配环境。
- 第一阶段已运行相关测试并临时启动本地模型试跑，临时进程已停止。未调用外部模型、未安装依赖、未提交 Git、未重启业务后端。

## 8. 第一阶段结果与下一步

- 实施说明：docs/rag-stage1-evaluation-20261028.md。
- 配置：RETRIEVAL_RERANK_CANDIDATE_TOP_K=8，证据目标 8；新键优先，旧键兼容。最初试验设置 20，但 CPU 成本较高，最终收敛为 8，未声称 8 已完成真实性能验证。
- SearchService 增加实际重排输入数、最终证据数与缺口。请求 evidence_top_k 不再被默认输入 6 限制，但可访问数量和文档上限仍生效。
- 新测试：test_rerank_budget.py、test_retrieval_evaluation.py；新增工具：scripts/evaluate-retrieval.py（export/capture/score）。
- 新题集：test-data/rag-evaluation/stage1-suite-v1.json。来源为已有 30 道题，全部 needs_review，不是新的人工金标准；语料快照包含项目下 2134 块、202 图片。
- 真实预算 6 首题耗时 59.591 秒，其中重排 55.746 秒；第二次约 59.373 秒。输入/最终证据均为 6，目标 8。8/20/40 未完成比较，部分采集标为 interrupted，不可计作完整对比。
- `.venv` 缺失基础 Python；使用 C:\Users\liwj\.conda\envs\design-rag\python.exe（3.11.15）。未改运行脚本或重建环境。原始 PG 探测漏做 SQLAlchemy DSN 规范化，后续用 normalized_postgres_dsn 已验证 PG 正常，不能把初始 ProgrammingError 视为数据库不可用。
- 临时模型进程已停止；本轮不重启业务后端，既有运行进程下次启动才加载新配置。
- 下一步：人工核实事实答案并补块/页码/资产 ID；按同一语料与角色完成可控的 6/8/20/40 实测。CPU 耗时约束需纳入默认预算选择；尚不进入图谱框架接入。

## 9. 可直接复制到新对话的启动语句

> 继续 I:\Design_RAG 的第一阶段收尾。先读 docs/rag-upgrade-handoff-20261028.md、docs/rag-upgrade-plan-20261028.md 和 docs/rag-stage1-evaluation-20261028.md。P00/P01 已完成，先核验实际代码；重点完成 P02 的真实题目证据标注与有预算的实测，不能把 needs_review 题目或中断采集当作准确率成绩。使用已有 Python 3.11 Conda 环境。保留当前 PostgreSQL/Qdrant、权限与显式外发边界；先不安装图谱框架或迁移数据库。


### 本次验证结果

变更前相关组 25 passed；扩展相关组 50 passed（2 条既有弃用警告）；最终预算及离线评测模块 22 passed。详细记录：artifacts/rag-stage1-20261028/verification.json。原有 test_retrieval_ranking.py 与修改前快照一致。
