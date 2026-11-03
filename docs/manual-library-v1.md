# 工作知识助手 V1（原独立日常手册库）

当前应用名为“工作知识助手”，新入口为 `http://127.0.0.1:8060/knowledge/`。旧入口与底层存储标识保持兼容。LangBot 与 Codex 接入详见 [接入指南](langbot-work-knowledge.md)。以下手册实现说明保留作为 V1 基础架构记录。

入口：[http://127.0.0.1:8060/manuals/](http://127.0.0.1:8060/manuals/)。只在本机监听，不对局域网开放。

## 日常使用

1. 点击左侧“资料分类”旁的加号创建分类，例如“软件操作”“设备手册”“日常流程”。
2. 点击“新建手册”，填写标题、分类、软件/设备和适用版本。
3. 上传 PDF、Word、PPT，或多张 PNG/JPG/WEBP 操作截图。
4. 文档可先用“本地提取”；PDF/DOCX/PPTX 支持本地文字及图片提取。扫描件、老 DOC/PPT 可明确提交 MinerU。仅上传不会外发。
5. 截图条目需要填写文字说明，并勾选对应图片。使用序号、上移/下移确定顺序；全部序号留空则作为原文片段保存。
6. 保存后检查内容，点击“发布手册”。草稿和历史版本不进入默认问答。
7. 在下方提问，查看原文步骤、配图和来源。可限定当前手册、软件/设备、版本与分类。长流程每页 20 段，可继续阅读，不会静默丢弃后续内容。

“整理简要说明”默认不勾选，直接看原文与配图较快。勾选后只将当前选中证据交给已配置的回答模型；原步骤顺序和图片不由模型改写。模型失败或引用不在证据范围时回退原文。

“识别截图文字”需要另行明确确认，将本地去除 EXIF 的图片发送给已配置的视觉模型；只识别可见文字/控件，不自动推断操作顺序或把观察升级为已确认步骤。识别结果可供整理时参考。

截图单张限制 20MiB，文档限制 200MiB。本地 PDF 提取不处理密码保护文件；无法提取文字时保留原件并提示改用 OCR，不伪造内容。DOCX 通常没有可靠页码，因此引用原文件及段落，不编造页码。PPTX 使用幻灯片页码。

## 启动与停止

使用已存在的 Python 3.11 环境，不依赖目前基础解释器缺失的项目 `.venv`：

```powershell
# 第一次初始化：创建新账号/数据库/索引，不覆盖设计库。
powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-manuals.ps1 -Initialize

# 后续启动
powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\start-manuals.ps1

# 只停止手册实例，不停止设计库、PostgreSQL、Qdrant 或共享模型。
powershell -NoProfile -ExecutionPolicy Bypass -File I:\Design_RAG\scripts\stop-manuals.ps1
```

PostgreSQL 与 Qdrant 需先处于运行状态。要使用语义检索，复用已有 8031 Embedding / 8032 Rerank 服务；不额外创建模型副本。未建立语义索引或服务不可用时，仍能按标题和原文关键词查询。内容保存后可明确点击“建立 / 重试语义索引”，其耗时取决于 CPU 和内容量；建立失败不会丢失权威原文。

## 隔离与版本

| 内容 | 手册库配置 |
| --- | --- |
| PostgreSQL | 数据库 `manual_rag`，账号 `manual_rag` |
| Qdrant | `manual_rag_chunks_v1` |
| 文件、截图、解析产物 | `I:\Design_RAG\data\manuals` |
| 私密实例配置 | `I:\Design_RAG\instances\manuals\.env`，Git 忽略 |
| 配置示例 | `instances/manuals/.env.example` |
| 后端入口 | `app.manual_main:app`，只监听 `127.0.0.1:8060` |
| 页面 | `apps/manual-web`，原生静态前端，无构建步骤 |

初始化仅显式复用原实例的模型/解析网关配置与本地模型服务地址，不复制设计库数据。图片签名密钥独立随机生成。新实例启动前检查数据库名、账号、索引、目录，误指向其他库时拒绝启动。

相同系列标识＋软件/设备＋适用版本发布新稿时，旧稿原子切换为历史版本。不同软件版本允许同时发布；查询命中同系列多个适用版本且未限定时，要求选择，不拼接不同版本的步骤。已发布条目不能原地改写，需创建新版。

新服务不导入旧 Agent 工作流、不挂载原设计库的 Admin/Agent/Harness 路由。图片只通过带签名的独立资产地址读取。写入接口需要本机请求标识，拒绝外部网页来源。本机 V1 不提供团队认证，不应通过代理或改监听地址开放到网络。

## API

- `GET/POST /api/v1/manuals/categories`：分类。
- `GET/POST /api/v1/manuals/entries`：列表和新建草稿。
- `GET /api/v1/manuals/entries/{id}`：完整流程及图片引用。
- `POST /api/v1/manuals/entries/{id}/files`：归档文档或截图。
- `PUT /api/v1/manuals/entries/{id}/steps`：保存明确的步骤和图片关联。
- `POST .../parse-local`、`.../submit-mineru`、`.../publish`、`.../index`：提取、明确提交、发布和索引。
- `POST /api/v1/manuals/ask`：问题、可选分类/软件/版本/条目、分页及 `source_only`；返回原文步骤、图片和来源，或版本选择/证据不足。
- `POST .../assets/{asset_id}/understand`：显式提交截图识别。

所有写请求带 `X-Manual-Request: 1`。外发请求还必须带 `explicit_external_export_approved: true`；前端已有确认提示。问答 `source_only: true` 完全不调用回答模型。语义调用只允许当前已发布记录对应的权威版本，旧向量即使保留也不会作为当前证据。

## 备份与恢复

先停止手册实例，让数据库与文件保持一致；保持 PostgreSQL、Qdrant 运行。使用现有 PostgreSQL 容器内的工具，不在命令行写密码：

```powershell
Set-Location I:\Design_RAG
$manualBackup = 'I:\Design_RAG\data\manuals-backup-20261029'
New-Item -ItemType Directory -Force -Path $manualBackup | Out-Null
$manualDbContainer = docker compose ps -q postgres
docker exec $manualDbContainer sh -c 'pg_dump -U "$POSTGRES_USER" -d manual_rag -Fc -f /tmp/manual-rag.dump'
docker cp "${manualDbContainer}:/tmp/manual-rag.dump" "$manualBackup\manual-rag.dump"
Copy-Item -LiteralPath I:\Design_RAG\data\manuals -Destination "$manualBackup\files" -Recurse
```

私密 `.env` 另行保存在你控制的私密位置，不发给他人、不放进 Git；恢复原账号密码和图片密钥时需要它。Qdrant 是可重建索引，库内内容和图片文件是必要备份。只备份向量库不能恢复手册。

恢复会替换**手册库**现有记录，先保留当前备份并停止手册实例。恢复到相同目录，检查目标名称确为 `manual_rag` 后执行：

```powershell
docker cp "$manualBackup\manual-rag.dump" "${manualDbContainer}:/tmp/manual-rag-restore.dump"
docker exec $manualDbContainer sh -c 'pg_restore -U "$POSTGRES_USER" -d manual_rag --role=manual_rag --clean --if-exists --no-owner /tmp/manual-rag-restore.dump'
# 将备份 files 目录中的内容恢复到 I:\Design_RAG\data\manuals；先移走现有目录，避免混合两个时间点的文件。
```

若向量索引也丢失，重新初始化独立 collection，并在手册页面逐项“建立 / 重试语义索引”。不要操作设计库的 collection。外发任务恢复后仍受原明确提交记录约束；恢复很久以前的快照前应先核对未完成任务。

## 验收与范围

验收素材明确标记“验收示例”：一份依据本次实际界面编写的完整 PDF，以及实际浏览器截图组成的图文条目。它们不是用户业务资料，也不是对真实手册质量的准确率基准。

验收记录、真实截图、问题与耗时保存在 `artifacts/manual-library-20261029/`。单元回归覆盖隔离、步骤顺序、版本选择、模型无效引用降级、无证据、长流程分页、图片关联和单并发。实际外部 MinerU 和视觉/回答网关需在你明确提交实际资料后验证；本轮验收不为测试目的外发文档或图片。
