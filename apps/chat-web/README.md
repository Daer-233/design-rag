# Design_RAG Chat Web

The React/Vite source lives in \`src/\`. The Chat page is served by the local Backend at \`/chat/\` and communicates with the anonymous Agent API under \`/api/v1/agent/*\`.

## Chat behavior

- **SSE reply stream**: \`answer.token\` events append the response as it arrives; reconnects replay persisted events using \`Last-Event-ID\`.
- **Execution summary**: the side panel shows visible workflow steps and concise, explainable route/retrieval summaries. It intentionally does **not** expose hidden model chain-of-thought.
- **Reply basis and citations**: completed answers show their route, rewritten retrieval question when used, execution summary, and the authorized knowledge-base citations returned by the backend.

## Build

Use the workspace build script:

\`\`\`powershell
powershell -ExecutionPolicy Bypass -File I:\\Design_RAG\\scripts\\build-chat-web.ps1
\`\`\`

It runs the Node/Vite bundle in memory and writes validated bundle bytes with .NET. This avoids the corrupt direct-file output that was observed with the local Node 24 environment. It only updates \`apps/chat-web/dist\` and does not start the Backend or submit MinerU/GPT tasks.

## 开源依赖与来源

### 实际使用

- [React / React DOM](https://github.com/facebook/react)：渲染 Web 界面。
- [Vite](https://github.com/vitejs/vite)：本地开发与前端构建。
- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react)：为 Vite 提供 React 编译支持。
