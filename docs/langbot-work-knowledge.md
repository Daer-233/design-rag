# 工作知识助手与 LangBot Codex 接入

最新检索链路已按用户确认升级为三路召回、统一重排及跨资料证据，见 [混合检索与 Codex 工作流](knowledge-hybrid-workflow.md)。本文件保留 LangBot 接入、Daemon 和本地修订的运维说明；最新验收以 `artifacts/knowledge-hybrid-20261029/verification.json` 为准。

更新：2026-10-29。当前方案采用用户指定的既有 **Codex** 处理器，连接既有 **工作知识** HTTP RAG 知识库。独立专用 Runner 源码保留为备选，未安装、未替换 Codex。

## 入口与数据流

- 本机资料管理与问答：<http://127.0.0.1:8060/knowledge/>；旧 `/manuals/` 继续兼容。
- LangBot 工作知识库：<http://localhost:5300/home/knowledge?id=b557ed18-fd7a-408c-89b7-fc9ffcc3e446>。
- Codex 处理器：`46b1174d-a808-46e3-ace9-ce34432803db`，Runner 为 `plugin:langbot-team/CodexAgent/default`。
- 数据流：Codex 授权知识工具 → LangBot HTTP RAG Connector → Docker `host.docker.internal:8060` → 本机独立工作知识服务。
- 这是实时接入，原件、图片与版本仍保存在独立 `manual_rag` 数据库和 `data/manuals`，不在 LangBot 重复上传一份。仅查询已发布且内容就绪的资料。

界面名称改为“工作知识助手”，资料叫“知识资料”，适用于 IT、入职、财务和运营。底层数据库、collection、文件路径、旧 API 名称保持兼容，不进行数据库重命名。当前原始资料仅有两份标明验收用途的示例，不代表已导入真实业务知识。

## HTTP 知识接口

`POST /api/v1/manuals/retrieve`，请求头 `X-Manual-Request: 1`。

```json
{"query":"如何申请 VPN？","top_k":5,"filters":{"version":"2026","page":1}}
```

返回 `results`，每条包含 `id`、`content`、`score`、`metadata`。完整原文页作为一条父块返回，保留顺序、文件、页码、证据 ID、逐步配图与章节配图。固定 `score=1` 只供连接器排序，明确标注为非置信度。无证据返回空结果；多版本返回选择说明；长资料明确提示后续页。

当前 HTTP RAG Connector 0.1.1 会过滤未声明的模板变量，因此接入模板只使用 `query` 与 `top_k`。续页或精确版本选择通过返回给 Codex 的只读 `@knowledge {"entry_id":"UUID","page":2}\n原问题` 查询信封完成。它只支持同一套严格查询条件，不能扩大到草稿、触发模型或执行写入。返回元数据用 `retrieval_page` 表示检索批次，原文件页码只读取每段的 `source.page`；缺少页码时明确说明，不能补写。

连接器元数据映射为 `knowledge=metadata`，不是 JSON 格式。图片签名引用位于 `metadata.knowledge.images`，并带 `evidence_ids`。本机原文、图片 URL 不保证能直接在远程手机打开；具体聊天平台图片投递需单独验证。知识接口始终 `source_only=true`，不调用回答模型；Codex 根据授权检索结果组织答案。

## 启动与运行

1. 启动 Docker Desktop 和原 LangBot 服务。
2. 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-manuals.ps1`。
3. 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-langbot-codex.ps1`。

Codex 容器没有 Windows 的 Codex 命令，使用官方 CodexAgent 0.1.20 的本机 Daemon 执行。网络安装曾持续缓慢，改为离线复用容器中实际安装的 `langbot-plugin==0.7.16` 与纯 Python `aiofiles`，保存到 `data/manuals/runtime/langbot-codex/sdk`；使用既有 Conda Python 与其通用依赖，不修改全局安装包。官方 `pkg` 源码及附带许可证从已安装插件产物复制到忽略目录，不修改上游代码。

轻量中继容器只发布 `127.0.0.1:18768`，转发到 Docker 内的官方 Hub `8768`。Hub 配置独立强随机 token；token 只在插件设置与本机 `daemon.private.json` 保存，不进入命令行、仓库或报告。启动器为官方 Daemon 准备私密认证副本和模型偏好，去掉全局管理 MCP，并只注入当次运行授权的 LangBot 知识桥；不会复制全局聊天历史。全局 Codex 配置不因本接入而改写。

### Codex 插件连续调用修复

实际连续问答发现官方 CodexAgent 0.1.20 的 Daemon Hub 长连接继承第一次调用的上下文：第一次问答成功，后续工具调用返回 `Plugin invocation has ended`。本地修订版本为 **`0.1.20+local.knowledge.1`**，不属于官方或认证发行版。

补丁只在任务分发时捕获当前有效 Context，在该任务的 MCP 请求中使用其副本；任务结束后的权限撤销检查保持有效。两项回归分别检查跨任务隔离和过期权限拒绝，补丁保存在 `patches/langbot-codex-daemon-context.patch`。构建/安装脚本为 `scripts/patch-langbot-codex-context.py`，只接受精确匹配的上游源码，不盲目应用到新版本。

原始官方产物仍保留在 Runtime 缓存中，更新前的配置另有备份；本地修订安装包、源码 SHA 和实际问答记录位于验收目录。以后升级 CodexAgent 应重新验证至少两次连续检索；如果官方已修复此问题，再移除本地修订。用户侧 Daemon 的源码与通信协议保持官方 0.1.20 版本。

停止本机执行连接：`scripts/stop-langbot-codex.ps1`。停止知识服务：`scripts/stop-manuals.ps1`。不停止 LangBot、原设计库、PostgreSQL、Qdrant 或共享模型。

## 配置与恢复

```powershell
# 使用已有 LANGBOT_API_KEY 用户环境变量，不把密钥放在命令行
& C:\Users\liwj\.conda\envs\design-rag\python.exe I:\Design_RAG\scripts\connect-langbot-knowledge.py configure
& C:\Users\liwj\.conda\envs\design-rag\python.exe I:\Design_RAG\scripts\connect-langbot-knowledge.py verify
```

`setup-daemon` 仅供初次设置或有意重配执行环境使用，会配置 Codex 官方插件的 Hub 与该处理器的本机执行地址；重配前先停止当前运行。不会绑定或改变已有机器人路由。

每次更新前的知识库、Codex 处理器与插件配置保存在忽略目录 `data/manuals/runtime/langbot-integration/before*.json`。这些备份可能含私密配置，不能提交到 Git。恢复时先停止 Daemon，再通过对应 LangBot PUT API 恢复备份中原知识库的创建/检索设置、原 Codex config 和插件 config；不要覆盖后续新增的其他处理器或机器人配置。

## 验证与交接

- 后端专项：`test_manual_library.py`、`test_langbot_knowledge.py`，验证存储隔离、版本、无证据、配图、原文分页和只读控制条件。
- 页面：`scripts/check-knowledge-ui.cjs`，检查新名称、旧入口兼容、四种宽度和固定输入区。
- 接入：`connect-langbot-knowledge.py verify` 必须返回匹配结果、文字与元数据均存在。
- Codex 实际模拟查询的结果与截图保存到 `artifacts/langbot-work-knowledge-20261029/`；验收以最终 JSON 记录为准。
- `tests/test_codex_daemon_context.py` 验证连续任务绑定各自上下文，且已结束的权限仍被拒绝。

新对话从本文件和 `docs/manual-library-handoff-20261029.md` 继续，先检查服务及最终验收 JSON。当前主路径是既有 Codex＋HTTP RAG；不要误安装备选专用 Runner，也不要因历史文件名含 manual 而重新创建数据库。

### 本轮最终验收

后端知识与隔离 26 项、Daemon 上下文与权限撤销 2 项测试通过，共 28 项；四种浏览器宽度布局通过。安装本地修订后，Codex 连续两次实际模拟问答通过：第一次返回正确操作与两条原文证据 ID，并明确原文件未提供页码；第二次财务审批问题无证据，明确未找到规定，不补写金额或人员。未向真实聊天群发送消息。

HTTP 知识连接已初始化，返回文字与图文元数据，两张受控缩略图可正常打开。当前共享语义服务未启动时使用关键词降级；完整聊天平台原图投递尚未验收。没有导入真实 IT、入职、财务或运营资料，不能把验收示例当作业务知识。详细记录为验收目录的 `verification.json`。
