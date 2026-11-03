# 工作知识助手 V1 交接（原日常手册库）

## 最新接入变更

最新检索阶段已经实现“向量＋稀疏＋标题关键词统一重排 → 分来源原文与图片 → 既有 LangBot Codex 回答”，发布后自动排队索引。优先阅读 [当前完整工作流](knowledge-hybrid-workflow.md)，其验收目录为 `artifacts/knowledge-hybrid-20261029/`。

2026-10-29 后续已按用户要求改名为“工作知识助手”，用途涵盖 IT、入职、财务与运营。新入口为 `http://127.0.0.1:8060/knowledge/`，旧 `/manuals/`、数据库及存储标识保持兼容。LangBot 接入改为用户指定的“工作知识”HTTP RAG 知识库＋既有 Codex 处理器，不安装备选专用 Runner。请优先阅读 [LangBot 工作知识接入与交接](langbot-work-knowledge.md)，实际验证结果见 `artifacts/langbot-work-knowledge-20261029/`。当前只有两份验收示例，尚无真实业务资料。

日期：2026-10-29。工作区 `I:\Design_RAG`。实施说明：[manual-library-v1.md](manual-library-v1.md)。

## 用户选择

独立入口；文档与截图都有；希望步骤配图；本机自己使用。用户已明确要求实施完整计划。

## 实现结构

- 独立 `manual_rag` 数据库/账号、`manual_rag_chunks_v1` 索引、`data/manuals` 文件目录、私密实例 `.env` 与随机图片签名密钥。
- 入口 `http://127.0.0.1:8060/manuals/`。脚本 `scripts/start-manuals.ps1`、`scripts/stop-manuals.ps1`，初始化/服务逻辑 `scripts/manual-instance.py`。
- `apps/backend/app/manual_main.py` 只组装手册路由，不导入旧 Agent 字节码或设计库 Admin；配置新增显式 env 文件选择，原实例默认仍读根 `.env`。
- `app/manuals/`：权威分类/版本/图片存储、文档本地提取、图文步骤、关键词及可用时的混合召回、模型说明与保守降级、MinerU 明确提交与任务轮询。
- 前端 `apps/manual-web`，原生静态文件，无打包依赖。三栏、独立滚动、固定输入区、响应式收起目录、减少动态效果。
- 复用父子块入库、MinerU provider、Qdrant provider、Embedding/Rerank 客户端和图片签名函数。新步骤页码来自原文件，不推断 DOCX 页码；章节图片与明确步骤配图区分。

## 运行与维护

使用 `C:\Users\liwj\.conda\envs\design-rag\python.exe`。项目 `.venv` 的基础 Python 仍缺失，未擅自重建。后续启动只启动手册服务；共享模型按需要由原有方式启动，不重复加载。

截图和文档归档不外发。MinerU/OCR 与截图识别必须明确提交。页面“整理简要说明”默认不勾选，原文步骤可直接返回；勾选后仅发送当前文字证据。回答失败或引用不合法时继续显示原文。

本机写请求要求 `X-Manual-Request: 1`，外发还需明确 true。禁止把当前本机 V1 直接开放到局域网。发布替换按系列＋产品＋版本进行，历史记录保留；不同适用版本不合并。

## 验证入口

- `apps/backend/tests/test_manual_library.py`：隔离、顺序、DOCX 本地提取、版本、分页、降级、图片关联、并发。
- `scripts/check-manual-ui.cjs`：真实 Edge/Playwright 创建、截图上传、步骤关联、发布、提问及布局验证。
- `scripts/create-manual-sample.py`：用已捆绑的 reportlab Python 生成自编验收手册，不是生产依赖。
- `scripts/check-manual-api.py`：十道有原文页码依据的问题、图片签名、文件预览、路由/来源/写请求边界和原设计库计数不变。
- 结果保存 `artifacts/manual-library-20261029/`，以最终验收 JSON 为准，不把测试用例通过等同于业务语料质量已验证。

## 环境注意事项

Windows 普通沙箱启动仍可能报 helper_unknown_error；本轮通过受审查的项目内执行完成。实际浏览器截图的 Node 二进制直写出现过损坏，验收脚本改用 base64 经 Python 落盘，并核验 PNG 签名。业务截图由 Python/Pillow 写入，真实上传流程已验证。不要用损坏的最初截图判断上传服务故障。

## 后续工作

1. 导入用户自己的真实手册，检查软件/版本、步骤与配图，再发布。
2. 用真实手册建立问题集；尚未实测的扫描件 MinerU、老 DOC/PPT、实际网关视觉与回答能力需有明确提交后验证。
3. 如需提升大手册检索，先评测章节/流程划分与定位，不直接加图谱或替换数据库。
4. 如需团队使用，单独实施身份、权限与部署方案，不能仅修改监听地址。

## 新对话启动语句

> 继续 I:\Design_RAG 的独立日常手册库。先阅读 docs/manual-library-handoff-20261029.md 和 docs/manual-library-v1.md，再检查实际代码与验收记录。服务入口为 127.0.0.1:8060/manuals/，独立 PG manual_rag、Qdrant manual_rag_chunks_v1 与 data/manuals。保留原设计库数据与配置；不要导入旧 Agent 工作流。下一步优先导入实际手册并验证步骤、图片、版本与引用，外发解析/视觉仍需明确提交。


## 最终验收状态

手册专项 17 passed；相关组合 46 passed；实际浏览器流程通过；10 道自编样本问题的来源页码检查通过，提取保留 10 个图片资产。原设计库计数未变。坏图 400、未批准外发 422、旧 Agent 未加载均通过。详见 docs/manual-library-acceptance-20261029.md 和 artifacts/manual-library-20261029/verification.json。服务已初始化并在本机 8060 运行。

## 2026-10-30：一键启动与重启入口

当前工作知识助手页面为 `http://127.0.0.1:8060/knowledge/`。项目根目录的 `start-knowledge.bat` 启动或复用后端 8060、共享 Embedding 8031、共享 Rerank 8032；`restart-knowledge.bat` 重启这三项服务。先打开 Docker Desktop，脚本会检查并按需启动已有数据库和 Qdrant 容器，随后核对应用健康状态。运行中的模型初始化进程也会复用，不重复加载。重启共享模型期间其他 RAG 查询会暂时受影响。

详细操作、日志和模型加载说明见 `docs/knowledge-one-click.md`。这两个入口不管理 LangBot 或 Codex Daemon；后者仍使用 `scripts/start-langbot-codex.ps1`。当前混合检索实现与验收以 `docs/knowledge-hybrid-workflow.md` 为准。
