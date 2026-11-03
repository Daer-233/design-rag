import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { chatApi } from './api'
import './styles.css'

const EVENT_LABELS = {
  'run.started': '已接收问题',
  'workflow.node_started': '开始执行',
  'workflow.node_completed': '执行完成',
  'intent.completed': '意图识别',
  'rewrite.completed': '问题重构',
  'retrieval.started': '知识库检索',
  'retrieval.completed': '检索与排序',
  'evidence.selected': '证据选择',
  'workflow.notice': '工作流提示',
  'answer.completed': '回答完成',
  'run.completed': '工作流完成',
  'run.error': '工作流异常',
  'run.cancelled': '已停止生成',
}

/**
 * Generate a request id in browsers/WebViews that may not implement
 * `crypto.randomUUID()` yet. It is used only for API idempotency, not security.
 */
export function createRequestId() {
  const cryptoApi = globalThis.crypto

  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID()
  }

  const bytes = new Uint8Array(16)
  if (typeof cryptoApi?.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80

  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function adminWorkspaceUrl() {
  const configured = (import.meta.env.VITE_DESIGN_RAG_ADMIN_WEB_URL || '').replace(/\/$/, '')
  if (configured) return configured
  const { protocol, hostname, port } = window.location
  if ((hostname === '127.0.0.1' || hostname === 'localhost') && port === '5174') {
    return `${protocol}//${hostname}:5173/admin/`
  }
  return '/admin/'
}

function formatTime(value) {
  if (!value) return ''
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(time)
}

function deduplicateImages(rawImages) {
  const seen = new Set()
  return (Array.isArray(rawImages) ? rawImages : []).filter((image) => {
    if (!image || typeof image !== 'object') return false
    const id = String(image.asset_id || '').trim()
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
}

function safeMessage(message) {
  const metadata = message?.metadata && typeof message.metadata === 'object' && !Array.isArray(message.metadata)
    ? message.metadata
    : {}
  return {
    ...message,
    citations: Array.isArray(message?.citations) ? message.citations : [],
    images: deduplicateImages(Array.isArray(message?.images) ? message.images : metadata.images),
    metadata,
  }
}

/**
 * Agent SSE and stored history may carry citation fields in different shapes.
 * New evidence packets use { chunk_id, citation: { file_name, ... } }, while
 * earlier history can contain the fields at the top level. Normalize both.
 */
export function normalizeCitation(rawCitation) {
  const raw = rawCitation && typeof rawCitation === 'object' && !Array.isArray(rawCitation) ? rawCitation : {}
  const nested = raw.citation && typeof raw.citation === 'object' && !Array.isArray(raw.citation) ? raw.citation : {}
  const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null && value !== '')

  return {
    source_id: firstDefined(nested.source_id, raw.source_id),
    document_id: firstDefined(nested.document_id, raw.document_id),
    project_id: firstDefined(nested.project_id, raw.project_id),
    project_name: firstDefined(nested.project_name, raw.project_name),
    file_name: firstDefined(nested.file_name, nested.document_name, raw.file_name, raw.document_name),
    file_type: firstDefined(raw.file_type, nested.file_type),
    page_start: firstDefined(raw.page_start, nested.page_start),
    page_end: firstDefined(raw.page_end, nested.page_end),
    section_path: firstDefined(raw.section_path, nested.section_path),
  }
}

function formatPageRange(citation) {
  if (citation.page_start === undefined && citation.page_end === undefined) return '页码未标注'
  if (citation.page_start === citation.page_end || citation.page_end === undefined) return `第 ${citation.page_start} 页`
  if (citation.page_start === undefined) return `截至第 ${citation.page_end} 页`
  return `第 ${citation.page_start}–${citation.page_end} 页`
}

export function deduplicateCitations(rawCitations) {
  const seen = new Set()
  return (Array.isArray(rawCitations) ? rawCitations : []).filter((rawCitation) => {
    const citation = normalizeCitation(rawCitation)
    const identity = [
      citation.project_id || citation.project_name || '',
      citation.document_id || citation.source_id || '',
      citation.file_name || '',
      citation.page_start ?? '',
      citation.page_end ?? '',
      citation.section_path || '',
    ].map((value) => String(value).trim().toLocaleLowerCase()).join('|')
    if (!identity.replaceAll('|', '')) return true
    if (seen.has(identity)) return false
    seen.add(identity)
    return true
  })
}
function CitationList({ citations }) {
  const uniqueCitations = deduplicateCitations(citations)
  if (!uniqueCitations.length) return null

  return (
    <details className="citations" open>
      <summary>引用证据（{uniqueCitations.length}）</summary>
      <div>
        {uniqueCitations.map((rawCitation, index) => {
          const citation = normalizeCitation(rawCitation)
          return (
            <article key={`${citation.source_id || index}-${citation.file_name || ''}`}>
              <strong>{citation.project_name || '项目资料'} · {citation.file_name || citation.source_id || '来源文件未标注'}</strong>
              <span>{citation.file_type ? String(citation.file_type).toUpperCase() : '资料类型未标注'} · {formatPageRange(citation)} · {citation.section_path || '章节未标注'}</span>
            </article>
          )
        })}
      </div>
    </details>
  )
}

function ImageReferenceList({ images }) {
  const uniqueImages = deduplicateImages(images)
  if (!uniqueImages.length) return null
  const labelFor = (image) => {
    if (image.evidence_type === 'document_fact' || image.asset_type === 'material_swatch') return '物料书依据'
    if (image.evidence_type === 'visual_observation' || image.evidence_type === 'vision_inferred') return '效果图参考'
    return '相关页面'
  }
  return (
    <details className="image-references" open>
      <summary>相关图片（{uniqueImages.length}）</summary>
      <div className="image-reference-grid">
        {uniqueImages.map((image) => {
          const title = image.document_name || image.file_name || '知识库图片'
          const page = image.page_number ? `第 ${image.page_number} 页` : '页码未标注'
          return (
            <a className="image-reference-card" key={image.asset_id} href={image.preview_url || image.original_url} target="_blank" rel="noreferrer">
              <img src={image.thumbnail_url || image.preview_url || image.original_url} alt={`${title} ${page}`} loading="lazy" />
              <span className="image-reference-caption">
                <strong>{labelFor(image)}</strong>
                <span>{title} · {page}</span>
                <small>{image.asset_type || '图片资产'}</small>
              </span>
            </a>
          )
        })}
      </div>
    </details>
  )
}

function AnswerBasis({ message }) {
  if (message.role !== 'assistant' || message.streaming) return null

  const metadata = message.metadata || {}
  const summary = typeof metadata.reasoning_summary === 'string' ? metadata.reasoning_summary : ''
  const intent = metadata.intent === 'professional'
    ? '专业检索问题'
    : metadata.intent === 'daily'
      ? '日常对话问题'
      : ''
  const rewritten = typeof metadata.rewritten_query === 'string' ? metadata.rewritten_query.trim() : ''
  const hasBasis = summary || intent || rewritten || message.content_origin
  if (!hasBasis) return null

  return (
    <details className="answer-basis">
      <summary>回复依据</summary>
      <div className="basis-grid">
        {intent && <section><span>处理路径</span><strong>{intent}</strong></section>}
        {summary && <section><span>执行摘要</span><strong>{summary}</strong></section>}
        {rewritten && rewritten !== message.content && <section><span>用于检索的问题</span><strong>{rewritten}</strong></section>}
        {message.content_origin === 'knowledge_base' && <section><span>证据范围</span><strong>回答受下方已授权项目资料约束。</strong></section>}
        {message.content_origin === 'general_llm' && <section><span>证据范围</span><strong>本轮未检索本地项目资料。</strong></section>}
      </div>
      <p className="basis-note">这里展示可解释的执行摘要与可追溯来源，不展示模型隐藏思维链。</p>
    </details>
  )
}

function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  return (
    <article className={`message ${isUser ? 'user' : 'assistant'}`}>
      <div className="message-avatar">{isUser ? '你' : 'DR'}</div>
      <div className="message-body">
        <div className="message-meta">
          <strong>{isUser ? '你' : 'Design_RAG Agent'}</strong>
          {message.content_origin === 'knowledge_base' && <span className="source-badge">知识库证据</span>}
          {message.content_origin === 'general_llm' && <span className="source-badge neutral">日常回答</span>}
          <time>{formatTime(message.created_at)}</time>
        </div>
        <div className="message-text">{message.content || (message.streaming ? <span className="typing">正在生成回答</span> : '')}</div>
        {message.streaming && <span className="cursor" />}
        <AnswerBasis message={message} />
        <CitationList citations={message.citations} />
        <ImageReferenceList images={message.images} />
      </div>
    </article>
  )
}

function EventPanel({ events, open, onToggle }) {
  const [tab, setTab] = useState('process')
  const summaries = events.filter((event) => event.payload?.summary)
  const intent = events.find((event) => event.event_type === 'intent.completed')
  const rewrite = events.find((event) => event.event_type === 'rewrite.completed')
  const retrieval = events.find((event) => event.event_type === 'retrieval.completed')

  return (
    <aside className={`workflow-panel ${open ? 'open' : 'collapsed'}`}>
      <button className="workflow-toggle" onClick={onToggle}>{open ? '隐藏执行面板' : '显示执行面板'}</button>
      {open && <>
        <div className="workflow-heading">
          <p className="eyebrow">AGENT WORKFLOW</p>
          <h2>本轮执行过程</h2>
          <p>展示步骤、简要依据和结果，不展示原始思维链。</p>
        </div>
        <div className="workflow-tabs" role="tablist" aria-label="工作流视图">
          <button className={tab === 'process' ? 'active' : ''} onClick={() => setTab('process')}>执行过程</button>
          <button className={tab === 'summary' ? 'active' : ''} onClick={() => setTab('summary')}>推理摘要</button>
          <button className={tab === 'details' ? 'active' : ''} onClick={() => setTab('details')}>工作流详情</button>
        </div>
        {tab === 'process' && (
          <ol className="event-timeline">
            {!events.length && <li className="muted">发送问题后将在这里显示流程。</li>}
            {events.filter((event) => !['answer.token', 'answer.citation'].includes(event.event_type)).map((event, index) => (
              <li key={`${event.id || index}-${event.event_type}`}>
                <span className={`event-dot ${event.event_type.includes('error') ? 'error' : event.event_type.includes('completed') ? 'done' : ''}`} />
                <div>
                  <strong>{EVENT_LABELS[event.event_type] || event.node_name || '工作流步骤'}</strong>
                  <p>{event.payload?.summary || event.payload?.label || '正在处理…'}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
        {tab === 'summary' && (
          <div className="summary-list">
            <section><strong>路由结果</strong><span>{intent?.payload?.summary || '等待识别'}</span></section>
            <section><strong>问题处理</strong><span>{rewrite?.payload?.summary || '日常问题不需要重构'}</span></section>
            <section><strong>检索结果</strong><span>{retrieval?.payload?.summary || '本轮未调用知识库'}</span></section>
            <section><strong>说明</strong><span>这里仅展示可解释的执行摘要，不包含模型的隐藏推理过程。</span></section>
          </div>
        )}
        {tab === 'details' && (
          <div className="detail-list">
            {!summaries.length && <p className="muted">暂无工作流详情。</p>}
            {summaries.map((event, index) => (
              <section key={`${event.id || index}-detail`}><code>{event.node_name || event.event_type}</code><span>{event.payload.summary}</span></section>
            ))}
          </div>
        )}
      </>}
    </aside>
  )
}

function ConversationList({ items, activeId, onSelect, onCreate, onDelete }) {
  return (
    <aside className="conversation-sidebar">
      <div className="brand"><div className="brand-badge">DR</div><div><strong>Design_RAG</strong><span>AGENT WORKSPACE</span></div></div>
      <button className="new-chat" onClick={onCreate}>＋ 新建对话</button>
      <div className="conversation-label">近期对话</div>
      <nav className="conversation-list" aria-label="近期对话">
        {!items.length && <p className="muted side-empty">还没有对话</p>}
        {items.map((item) => (
          <div className={`conversation-item ${item.id === activeId ? 'active' : ''}`} key={item.id}>
            <button onClick={() => onSelect(item.id)}><strong>{item.title || '新对话'}</strong><span>{item.last_message || '开始一段新的对话'}</span></button>
            <button className="delete-chat" aria-label="删除对话" onClick={(event) => { event.stopPropagation(); onDelete(item.id) }}>×</button>
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <a className="ai-switch-link" href={adminWorkspaceUrl()} aria-label="前往知识库管理后台"><span className="ai-switch-icon" aria-hidden="true">✦</span><span className="ai-switch-copy"><small>KNOWLEDGE BASE</small><strong>进入知识库管理</strong></span><span className="ai-switch-arrow" aria-hidden="true">↗</span></a>
        <div className="sidebar-note">临时匿名模式<br />同一浏览器的聊天记录独立保存</div>
      </div>
    </aside>
  )
}

function App() {
  const [conversations, setConversations] = useState([])
  const [projects, setProjects] = useState([])
  const [activeConversation, setActiveConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [selectedProject, setSelectedProject] = useState('')
  const [events, setEvents] = useState([])
  const [workflowOpen, setWorkflowOpen] = useState(() => window.innerWidth > 1180)
  const [running, setRunning] = useState(false)
  const [currentRunId, setCurrentRunId] = useState(null)
  const [error, setError] = useState('')
  const scrollRef = useRef(null)
  const eventSourceRef = useRef(null)
  const activeConversationRef = useRef(null)
  const activeConversationInfo = useMemo(() => conversations.find((item) => item.id === activeConversation), [conversations, activeConversation])

  useEffect(() => { activeConversationRef.current = activeConversation }, [activeConversation])

  async function refreshConversations() {
    const result = await chatApi.listConversations()
    const items = result.items || []
    setConversations(items)
    return items
  }

  async function loadConversation(id) {
    setError('')
    const result = await chatApi.getConversation(id)
    setActiveConversation(id)
    setMessages((result.messages || []).map(safeMessage))
    setSelectedProject(result.conversation?.project_id || '')
    setEvents([])
  }

  async function createConversation() {
    try {
      const result = await chatApi.createConversation(selectedProject || null)
      await refreshConversations()
      await loadConversation(result.conversation.id)
    } catch (err) {
      setError(err.message)
    }
  }

  async function deleteConversation(id) {
    if (!window.confirm('确定删除这个对话及其本地聊天记录吗？')) return
    try {
      await chatApi.deleteConversation(id)
      const items = await refreshConversations()
      if (id === activeConversationRef.current) {
        setMessages([])
        setEvents([])
        setActiveConversation(null)
        if (items[0]) await loadConversation(items[0].id)
      }
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    ;(async () => {
      try {
        await chatApi.getSession()
        const [items, projectResult] = await Promise.all([refreshConversations(), chatApi.listProjects().catch(() => ({ items: [] }))])
        setProjects(projectResult.items || [])
        if (items[0]) await loadConversation(items[0].id)
      } catch (err) {
        setError(err.message)
      }
    })()
    return () => eventSourceRef.current?.close()
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, events])

  function handleEvent(name, event) {
    if (name === 'heartbeat') return
    const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
    if (name !== 'answer.token') setEvents((items) => [...items, { ...event, payload, event_type: name }])

    if (name === 'answer.token') {
      setMessages((items) => items.map((item) => item.localStreaming ? { ...item, content: `${item.content || ''}${payload.text || ''}` } : item))
    }
    if (name === 'retrieval.images' || name === 'retrieval_images') {
      setMessages((items) => items.map((item) => item.localStreaming
        ? { ...item, images: deduplicateImages([...(item.images || []), ...(payload.images || [])]) }
        : item))
    }
    if (name === 'answer.citation') {
      setMessages((items) => items.map((item) => item.localStreaming ? {
        ...item,
        citations: Array.isArray(payload.citations) ? payload.citations : item.citations,
        images: deduplicateImages([...(item.images || []), ...(payload.images || [])]),
      } : item))
    }
    if (name === 'answer.completed') {
      setMessages((items) => items.map((item) => item.localStreaming ? {
        ...item,
        content_origin: payload.content_origin || item.content_origin,
        metadata: {
          ...(item.metadata || {}),
          intent: payload.intent || item.metadata?.intent,
          rewritten_query: payload.rewritten_query || item.metadata?.rewritten_query,
          reasoning_summary: payload.reasoning_summary || item.metadata?.reasoning_summary,
          images: deduplicateImages([...(item.metadata?.images || []), ...(payload.images || [])]),
        },
      } : item))
    }

    if (['run.completed', 'run.error', 'run.cancelled'].includes(name)) {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      setRunning(false)
      setCurrentRunId(null)
      setMessages((items) => items.map((item) => item.localStreaming ? { ...item, localStreaming: false, streaming: false } : item))
      refreshConversations().catch(() => {})
      const conversationId = activeConversationRef.current
      if (conversationId) chatApi.getConversation(conversationId).then((result) => setMessages((result.messages || []).map(safeMessage))).catch(() => {})
      if (name === 'run.error') setError(payload.summary || '工作流执行失败')
    }
  }

  async function sendMessage() {
    const query = draft.trim()
    if (!query || running) return

    try {
      setError('')
      let conversationId = activeConversationRef.current
      if (!conversationId) {
        const created = await chatApi.createConversation(selectedProject || null)
        conversationId = created.conversation.id
        await refreshConversations()
        setActiveConversation(conversationId)
      }

      const createdAt = new Date().toISOString()
      const optimisticUser = { id: `local-user-${Date.now()}`, role: 'user', content: query, content_origin: 'user', created_at: createdAt }
      const optimisticAssistant = { id: `local-assistant-${Date.now()}`, role: 'assistant', content: '', content_origin: '', citations: [], images: [], metadata: {}, created_at: createdAt, streaming: true, localStreaming: true }
      setMessages((items) => [...items, optimisticUser, optimisticAssistant])
      setEvents([])
      setDraft('')
      setRunning(true)

      const runResult = await chatApi.createRun(conversationId, {
        query,
        projectId: selectedProject || null,
        requestId: createRequestId(),
      })
      const runId = runResult.run.id
      setSelectedProject(runResult.run.project_id || '')
      setCurrentRunId(runId)
      eventSourceRef.current?.close()
      eventSourceRef.current = chatApi.openEvents(runId, handleEvent, () => {})
      await refreshConversations()
    } catch (err) {
      setRunning(false)
      setMessages((items) => items.filter((item) => !item.localStreaming && !String(item.id).startsWith('local-user-')))
      setError(err.message)
    }
  }

  async function stopRun() {
    if (!currentRunId) return
    try {
      await chatApi.cancelRun(currentRunId)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="app-shell">
      <ConversationList items={conversations} activeId={activeConversation} onSelect={loadConversation} onCreate={createConversation} onDelete={deleteConversation} />
      <main className="chat-main">
        <header className="chat-header">
          <div><p className="eyebrow">ANONYMOUS LOCAL AGENT</p><h1>{activeConversationInfo?.title || 'Design_RAG 智能体'}</h1><p>专业问题检索项目知识库；日常问题直接对话。</p></div>
          <div className="project-picker"><label>检索范围</label><select value={selectedProject} onChange={(event) => setSelectedProject(event.target.value)} disabled={running}><option value="">全部已索引项目</option>{projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select></div>
        </header>
        {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError('')}>×</button></div>}
        <section className="message-list" ref={scrollRef}>
          {!messages.length && <div className="empty-state"><div className="empty-icon">✦</div><h2>从一个问题开始</h2><p>例如：绿城下沙项目有哪些可确认的材料信息？<br />或：帮我解释一下什么是软装搭配。</p></div>}
          {messages.map((message) => <MessageBubble message={message} key={message.id} />)}
        </section>
        <footer className="composer-wrap">
          <div className="composer">
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage() } }} placeholder="输入你的问题。Enter 发送，Shift + Enter 换行" disabled={running} />
            <div className="composer-actions"><span>{running ? 'Agent 正在执行工作流…' : '专业问题将自动查询知识库'}</span>{running ? <button className="stop-button" onClick={stopRun}>停止生成</button> : <button className="send-button" onClick={sendMessage} disabled={!draft.trim()}>发送 <b>↗</b></button>}</div>
          </div>
        </footer>
      </main>
      <EventPanel events={events} open={workflowOpen} onToggle={() => setWorkflowOpen((open) => !open)} />
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)