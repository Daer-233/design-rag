# Design_RAG 管理后台（React）

这是一个**本机运维界面**，用于查看 PostgreSQL 中的检索审计记录：谁提问、问题内容、检索项目、返回证据数、检索耗时和失败/无结果信号。它不读取 Qdrant，不读取原始文件，也不展示源文件路径或原始来源定位。

## 一次性构建与访问

此电脑的 Windows Node.js 进程会被本机环境拦截：写入一个短文本也会变成约 4 KB 的二进制内容。因此不要在宿主机上执行 `pnpm install`、`npm install` 或 `npm run build`。

推荐使用项目提供的 Docker 构建脚本。它在 Linux 容器内运行 Node.js，并只把正常的静态构建文件写回项目目录：

```powershell
Set-Location I:\Design_RAG
.\scripts\build-admin-web.ps1
```

该方式需要 Docker Desktop 处于运行状态。脚本会验证 `dist\index.html` 是正常 HTML；如校验失败则不应重启后端或对外使用页面。

然后启动或重启**完整本地后端**（不是 `app.remote_main`）：

```powershell
$env:PYTHONPATH = 'I:\Design_RAG\apps\backend'
C:\Users\liwj\.conda\envs\design-rag\python.exe -c "from app.db.migrations import apply_migrations; print(apply_migrations())"
C:\Users\liwj\.conda\envs\design-rag\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8030
```

在本机浏览器打开：`http://127.0.0.1:8030/admin/`。

构建后的 React 文件由完整本地后端同源托管，因此不需要开放管理 API 的跨域访问，也不会出现在网络侧的 `app.remote_main`（8030）中。

## 开发模式

当前不建议在这台主机上运行 `pnpm run dev`，因为它仍会调用会损坏写入结果的宿主机 Node.js。请使用上方 Docker 构建脚本生成生产静态文件，并通过本地后端的 `/admin/` 查看页面。

## 提问者身份记录

每次通过认证的 `POST /api/v1/search` 都会写入 PostgreSQL。默认提问者是服务端配置的 `DESIGN_RAG_API_PRINCIPAL_USER_ID`。如果每台 OpenClaw 调用端能提供更精确身份，可在请求中附加：

```text
X-Design-RAG-Requester-ID: openclaw-alice
X-Design-RAG-Requester-Name: Alice
```

这两个字段被记录为“调用端声明身份”，便于公司内优化检索；它们不是权限凭证，权限仍由 Bearer Token 和服务端项目白名单决定。

## 管理 API

仅完整本地后端 `app.main` 挂载以下接口；网络侧的 `app.remote_main` 不包含它们：

- `GET /api/v1/admin/projects`
- `GET /api/v1/admin/search-audit`
- `GET /api/v1/admin/search-audit/summary`
