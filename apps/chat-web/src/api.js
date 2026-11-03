const configuredBase = (import.meta.env?.VITE_DESIGN_RAG_CHAT_API_BASE_URL || '').replace(/\/$/, '')
const localBase = 'http://127.0.0.1:8030'

function baseUrl() {
  return configuredBase || (window.location.origin === 'null' ? localBase : '')
}

function apiUrl(path) {
  return `${baseUrl()}${path}`
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(apiUrl(path), {
      credentials: 'include',
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    })
  } catch {
    throw new Error('无法连接 Design_RAG Backend :8030。请确认正式 Backend 已启动。')
  }
  if (response.status === 204) return null
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail || `请求失败（HTTP ${response.status}）`)
  return body
}

export const chatApi = {
  getSession: () => request('/api/v1/agent/session'),
  listConversations: () => request('/api/v1/agent/conversations'),
  createConversation: (projectId) => request('/api/v1/agent/conversations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId || null }),
  }),
  getConversation: (id) => request(`/api/v1/agent/conversations/${id}`),
  renameConversation: (id, title) => request(`/api/v1/agent/conversations/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
  }),
  deleteConversation: (id) => request(`/api/v1/agent/conversations/${id}`, { method: 'DELETE' }),
  createRun: (conversationId, { query, projectId, requestId }) => request(`/api/v1/agent/conversations/${conversationId}/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, project_id: projectId || null, request_id: requestId }),
  }),
  cancelRun: (runId) => request(`/api/v1/agent/runs/${runId}/cancel`, { method: 'POST' }),
  listProjects: () => request('/api/v1/agent/projects'),
  openEvents(runId, onEvent, onError) {
    const source = new EventSource(apiUrl(`/api/v1/agent/runs/${runId}/events`), { withCredentials: true })
    const names = [
      'run.started', 'workflow.node_started', 'workflow.node_completed', 'intent.completed', 'rewrite.completed',
      'retrieval.started', 'retrieval.completed', 'retrieval.images', 'retrieval_images', 'evidence.selected', 'workflow.notice', 'answer.token',
      'answer.citation', 'answer.completed', 'run.error', 'run.completed', 'run.cancelled', 'heartbeat',
    ]
    names.forEach((name) => source.addEventListener(name, (event) => {
      try { onEvent(name, JSON.parse(event.data), event.lastEventId) } catch { onEvent(name, {}, event.lastEventId) }
    }))
    source.onerror = () => onError?.(source)
    return source
  },
}
