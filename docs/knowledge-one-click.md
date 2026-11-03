# 工作知识助手一键启动与重启

项目目录 `I:\Design_RAG` 中有两个可双击的入口：

| 文件 | 用途 |
|---|---|
| `start-knowledge.bat` | 启动后端 8060、Embedding 8031、Rerank 8032，复用已运行的服务 |
| `restart-knowledge.bat` | 停止并重新启动上述三个服务，适合配置变更或服务异常时使用 |

先打开 Docker Desktop，再双击对应文件。脚本会检查现有 PostgreSQL、Qdrant 容器；已停止时启动原容器，等待健康检查通过，再启动应用服务。缺少容器或配置时显示失败原因，不自动创建新库或重建索引。

看到三个 `OK` 后，打开 <http://127.0.0.1:8060/knowledge/>。服务在后台运行，可以关闭启动窗口。模型加载方式沿用现有 `.env` 配置；当前配置为启动时加载，CPU 初始化可能需要几分钟，脚本对每项服务最多等待 5 分钟。如果将来改为懒加载，`model loads on first query` 表示服务已监听、模型将在第一次查询时加载。

重启会中断正在执行的知识查询。8031、8032 是共享模型服务，使用它们的设计 RAG 查询也会暂时受影响；脚本不重启设计后端 8030、数据库、LangBot 或 Codex 连接服务。

脚本在停止任何服务前核对全部目标进程归属；端口被其他程序占用、PID 归属不符时停止操作，并显示错误。重复双击时只允许一个启动或重启操作进行。

日志位置：

- 启动入口：`data\manuals\runtime\launcher-日期-时间.log`
- 后端：`data\manuals\runtime\server.out.log`、`server.err.log`
- 模型：`data\manuals\runtime\shared-models\embedding.out.log`、`embedding.err.log`、`rerank.out.log`、`rerank.err.log`

自动化调用可追加 `--no-pause` 跳过末尾按键等待；成功退出码为 0，失败为 1。例如：

```powershell
.\start-knowledge.bat --no-pause
.\restart-knowledge.bat --no-pause
```

如果要通过 LangBot 的 Codex 处理器回答，还需单独运行现有 `scripts\start-langbot-codex.ps1`。这两个 BAT 只管理知识后端和两个共享模型服务。
