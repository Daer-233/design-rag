import { memo, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { createPortal } from 'react-dom'
import dayjs from 'dayjs'
import { DatePicker, Radio, Tabs, Tree } from 'antd'
import { AppstoreOutlined, BarChartOutlined, BulbOutlined, CloudUploadOutlined, FileExcelOutlined, FileImageOutlined, FileOutlined, FilePdfOutlined, FilePptOutlined, FileTextOutlined, FileUnknownOutlined, FileWordOutlined, FileZipOutlined, FolderOutlined, FundProjectionScreenOutlined, HistoryOutlined, InboxOutlined } from '@ant-design/icons'
import { adminApi } from './api'
import PdfPageReview from './PdfPageReview'
import { collectDroppedFiles, fileDisplayPath, inferProjectDefaults } from './drop-files'
import 'antd/dist/reset.css'
import './styles.css'

const PAGE_SIZE = 50
const OUTCOME_LABEL = { succeeded: '成功', denied: '拒绝', failed: '失败' }
const NAV_SECTIONS = [
  {
    label: '工作台',
    items: [
      { id: 'overview', label: '检索概览', icon: AppstoreOutlined },
      { id: 'import', label: '导入项目', icon: CloudUploadOutlined },
    ],
  },
  {
    label: '分析与核对',
    items: [
      { id: 'analysis', label: '视觉分析', icon: BulbOutlined },
      { id: 'material-consistency', label: '效果图物料核对', icon: FundProjectionScreenOutlined },
      { id: 'quality', label: '知识库质量评测', icon: BarChartOutlined },
      { id: 'harness', label: 'Design Harness', icon: FundProjectionScreenOutlined },
    ],
  },
  {
    label: '导入记录',
    items: [
      { id: 'batch-records', label: '批次导入文件明细', icon: InboxOutlined },
      { id: 'import-records', label: '导入任务记录', icon: HistoryOutlined },
    ],
  },
]

function chatWorkspaceUrl() {
  if (import.meta.env.DEV && window.location.port === '5173') {
    return `${window.location.protocol}//${window.location.hostname}:5174/chat/`
  }
  return '/chat/'
}

function formatDate(value) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', hour12: false }).format(new Date(value)) : '—' }
function disabledScheduleTime(current) {
  if (!current || !current.isSame(dayjs(), 'day')) return {}
  const now = dayjs()
  const currentHour = now.hour()
  const currentMinute = now.minute()
  return {
    disabledHours: () => Array.from({ length: currentHour }, (_, index) => index),
    disabledMinutes: (selectedHour) => selectedHour === currentHour
      ? Array.from({ length: Math.floor(currentMinute / 5) + 1 }, (_, index) => index * 5)
      : [],
  }
}
function formatBytes(value) { if (!value) return '—'; const units = ['B', 'KB', 'MB', 'GB']; let n = value; let i = 0; while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }; return `${n.toFixed(i ? 1 : 0)} ${units[i]}` }
function MetricCard({ label, value, detail, tone = 'neutral' }) { return <article className={`metric-card ${tone}`}><p>{label}</p><strong>{value}</strong><small>{detail}</small></article> }
function OutcomeBadge({ outcome }) { return <span className={`badge badge-${outcome}`}>{OUTCOME_LABEL[outcome] || outcome}</span> }
function StatusBadge({ status }) { const label = { created: '待提交', awaiting_approval: '等待授权', skipped: '重复跳过', awaiting_page_review: '等待分页审核', scheduled: '已定时', scheduling: '正在按计划入队', queued: '排队中', running: '分析中', processing: '处理中', submitted: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[status] || status; return <span className={`badge status-${status}`}>{label}</span> }
function isActiveImportRecord(record) { return ['queued', 'processing', 'submitted'].includes(record.status) || ['queued', 'running'].includes(record.visual_analysis_status) }
function ModalPortal({ children }) { return createPortal(children, document.body) }

function createImportRecordStore() {
  let rows = new Map()
  const listeners = new Map()
  return {
    replace(records) { rows = new Map(records.map((record) => [record.id, record])) },
    get(jobId, fallback) { return rows.get(jobId) || fallback },
    subscribe(jobId, listener) {
      const current = listeners.get(jobId) || new Set()
      current.add(listener)
      listeners.set(jobId, current)
      return () => {
        current.delete(listener)
        if (!current.size) listeners.delete(jobId)
      }
    },
    update(record) {
      const current = rows.get(record.id)
      if (current && JSON.stringify(current) === JSON.stringify(record)) return false
      rows.set(record.id, record)
      listeners.get(record.id)?.forEach((listener) => listener())
      return true
    },
  }
}

function OverviewPage({ projects, setProjects }) {
  const [summary, setSummary] = useState(null); const [rows, setRows] = useState([]); const [total, setTotal] = useState(0); const [offset, setOffset] = useState(0); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [selected, setSelected] = useState(null); const [filters, setFilters] = useState({ project_id: '', actor_id: '', query: '', outcome: '', days: 30 })
  const load = useCallback(async (nextOffset = 0, activeFilters = filters) => { setLoading(true); setError(''); try { const since = new Date(Date.now() - Number(activeFilters.days) * 86400000).toISOString(); const [p, s, l] = await Promise.all([adminApi.getProjects(), adminApi.getSummary(activeFilters.days), adminApi.getSearchAudit({ limit: PAGE_SIZE, offset: nextOffset, ...activeFilters, since })]); setProjects(p.items || []); setSummary(s); setRows(l.items || []); setTotal(l.total || 0); setOffset(nextOffset) } catch (e) { setError(e.message || '无法读取后台数据') } finally { setLoading(false) } }, [filters, setProjects])
  useEffect(() => { load(0, filters) }, [])
  const successRate = useMemo(() => !summary?.total_searches ? '—' : `${Math.round((summary.succeeded_searches / summary.total_searches) * 100)}%`, [summary]); const page = Math.floor(offset / PAGE_SIZE) + 1; const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }))
  function submit(e) { e.preventDefault(); load(0, filters) }
  return <section className="content-page">
    <header className="page-header"><div><p className="eyebrow">LOCAL OPERATIONS</p><h1>检索概览</h1><p className="subtitle">查看项目、检索质量和知识库的使用情况。</p></div><button className="secondary-button" onClick={() => load(offset, filters)} disabled={loading}>刷新数据</button></header>
    {error && <section className="error-banner"><strong>无法读取后台：</strong>{error}</section>}
    <section className="metrics"><MetricCard label="检索总数" value={summary?.total_searches ?? '—'} detail={`最近 ${filters.days} 天`} /><MetricCard label="成功率" value={successRate} detail={`${summary?.succeeded_searches ?? 0} 次成功`} tone="positive" /><MetricCard label="无结果问题" value={summary?.zero_result_searches ?? '—'} detail="优先补充这些问题" tone="warning" /><MetricCard label="平均耗时" value={summary ? `${summary.average_latency_ms} ms` : '—'} detail={`${summary?.failed_searches ?? 0} 次失败`} tone="cool" /></section>
    <section className="panel"><div className="panel-heading"><div><p className="eyebrow">SEARCH AUDIT</p><h2>检索记录</h2></div><span>{total} 条记录</span></div><form className="filters" onSubmit={submit}><label>项目<select value={filters.project_id} onChange={(e) => update('project_id', e.target.value)}><option value="">全部项目</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.external_key}</option>)}</select></label><label>提问者<input placeholder="姓名或调用端" value={filters.actor_id} onChange={(e) => update('actor_id', e.target.value)} /></label><label>问题关键词<input placeholder="材料、照明、户型" value={filters.query} onChange={(e) => update('query', e.target.value)} /></label><label>结果<select value={filters.outcome} onChange={(e) => update('outcome', e.target.value)}><option value="">全部状态</option><option value="succeeded">成功</option><option value="denied">拒绝</option><option value="failed">失败</option></select></label><label>时间<select value={filters.days} onChange={(e) => update('days', Number(e.target.value))}><option value={7}>近 7 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option><option value={365}>近 1 年</option></select></label><div className="filter-actions"><button className="primary-button" type="submit" disabled={loading}>筛选</button></div></form><div className="table-wrap"><table><thead><tr><th>时间</th><th>提问者</th><th>问题</th><th>项目</th><th>结果</th><th>耗时</th><th /></tr></thead><tbody>{loading && <tr><td colSpan="7" className="empty">正在读取记录…</td></tr>}{!loading && !rows.length && <tr><td colSpan="7" className="empty">还没有符合条件的检索记录。</td></tr>}{!loading && rows.map((row) => <tr key={row.id} className={selected?.id === row.id ? 'selected-row' : ''}><td className="time">{formatDate(row.requested_at)}</td><td><strong>{row.actor_display_name || row.actor_id}</strong></td><td className="query-cell">{row.query_text}</td><td>{row.project.name}<small>{row.project.external_key}</small></td><td><OutcomeBadge outcome={row.outcome} /><small>{row.result_count} 条证据</small></td><td>{row.latency_ms == null ? '—' : `${row.latency_ms} ms`}</td><td><button className="detail-button" onClick={() => setSelected(row)}>查看</button></td></tr>)}</tbody></table></div><footer className="pagination"><span>第 {page} / {pageCount} 页</span><div><button className="secondary-button" disabled={offset === 0 || loading} onClick={() => load(Math.max(0, offset - PAGE_SIZE), filters)}>上一页</button><button className="secondary-button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => load(offset + PAGE_SIZE, filters)}>下一页</button></div></footer></section>
    {selected && <ModalPortal><div className="modal-backdrop" onMouseDown={() => setSelected(null)}><aside className="detail-drawer" onMouseDown={(e) => e.stopPropagation()}><button className="close-button" onClick={() => setSelected(null)}>×</button><p className="eyebrow">SEARCH DETAIL</p><h2>检索记录详情</h2><dl><dt>提问内容</dt><dd className="question-detail">{selected.query_text}</dd><dt>项目</dt><dd>{selected.project.name}</dd><dt>结果</dt><dd><OutcomeBadge outcome={selected.outcome} /> {selected.result_count} 条安全证据</dd><dt>时间</dt><dd>{formatDate(selected.requested_at)}</dd></dl><p className="privacy-note">地页只展示检索运维数据和安全 Chunk 标识，不展示地地源文件路径。</p></aside></div></ModalPortal>}
  </section>
}

function buildFileTree(files) {
  const root = { folders: new Map(), files: [] }
  for (const file of files) {
    const segments = fileDisplayPath(file).replace(/\\/g, '/').split('/').filter(Boolean)
    const fileName = segments.at(-1) || file.name
    let node = root
    for (const folderName of segments.slice(0, -1)) {
      if (!node.folders.has(folderName)) node.folders.set(folderName, { folders: new Map(), files: [] })
      node = node.folders.get(folderName)
    }
    node.files.push({ name: fileName, file })
  }
  return root
}

function countTreeFiles(node) {
  return node.files.length + [...node.folders.values()].reduce((total, child) => total + countTreeFiles(child), 0)
}

function fileExtension(fileName) {
  const extension = String(fileName || '').split('.').pop()?.toLowerCase()
  return extension && extension !== fileName.toLowerCase() ? extension : ''
}

const MARKITDOWN_FILE_TYPES = new Set(['docx', 'xlsx', 'xls', 'csv', 'html', 'htm', 'md', 'markdown'])
const VISION_FILE_TYPES = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'])
function fileDraftKey(file) { return `${fileDisplayPath(file)}-${file.size}-${file.lastModified}` }

function FileTypeIcon({ fileName }) {
  const extension = fileExtension(fileName)
  const className = `file-type-icon type-${extension || 'unknown'}`
  if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'svg', 'tif', 'tiff', 'heic'].includes(extension)) return <FileImageOutlined className={className} />
  if (extension === 'pdf') return <FilePdfOutlined className={className} />
  if (['doc', 'docx', 'rtf', 'odt'].includes(extension)) return <FileWordOutlined className={className} />
  if (['ppt', 'pptx', 'key'].includes(extension)) return <FilePptOutlined className={className} />
  if (['xls', 'xlsx', 'csv', 'numbers'].includes(extension)) return <FileExcelOutlined className={className} />
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(extension)) return <FileZipOutlined className={className} />
  if (['txt', 'md', 'markdown', 'json', 'xml', 'yaml', 'yml', 'html', 'css', 'js', 'jsx', 'ts', 'tsx', 'py'].includes(extension)) return <FileTextOutlined className={className} />
  return extension ? <FileOutlined className={className} /> : <FileUnknownOutlined className={className} />
}

function folderKey(path) { return `folder:${path}` }

function toTreeData(node, parentPath = '', processorSelections = {}, disabled = false, onProcessorChange) {
  const folders = [...node.folders.entries()].sort(([a], [b]) => a.localeCompare(b, 'zh-CN'))
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  return [
    ...folders.map(([name, child]) => {
      const path = parentPath ? `${parentPath}/${name}` : name
      return {
        key: folderKey(path),

        title: <span className="tree-node-title"><FolderOutlined className="tree-node-file-icon folder-type-icon" /><span className="tree-node-name">{name}</span><small>{countTreeFiles(child)} 个文件</small></span>,
        children: toTreeData(child, path, processorSelections, disabled, onProcessorChange),
      }
    }),
    ...files.map(({ name, file }) => ({
      key: `file:${fileDisplayPath(file)}-${file.size}-${file.lastModified}`,

      title: <span className="tree-node-title"><FileTypeIcon fileName={name} /><span className="tree-node-name">{name}</span><small>{formatBytes(file.size)}</small><select className="file-processor-select" aria-label={`${name} 的处理方式`} value={processorSelections[fileDraftKey(file)] || 'auto'} disabled={disabled} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); onProcessorChange?.(fileDraftKey(file), event.target.value) }}><option value="auto">自动选择</option>{MARKITDOWN_FILE_TYPES.has(fileExtension(name)) && <option value="markitdown">本地 MarkItDown</option>}{!VISION_FILE_TYPES.has(fileExtension(name)) && <option value="mineru">MinerU</option>}</select></span>,
      isLeaf: true,
    })),
  ]
}

function FileTree({ node, processorSelections = {}, disabled = false, onProcessorChange }) {
  const treeData = useMemo(() => toTreeData(node, '', processorSelections, disabled, onProcessorChange), [node, processorSelections, disabled, onProcessorChange])
  const topLevelKeys = useMemo(() => [...node.folders.keys()].sort((a, b) => a.localeCompare(b, 'zh-CN')).map(folderKey), [node])
  return <Tree className="file-tree" blockNode defaultExpandedKeys={topLevelKeys} treeData={treeData} />
}
function DocumentPageVisionPlan({ plan, tasks }) {
  const pages = Array.isArray(plan?.pages) ? plan.pages : []
  const taskByPage = new Map(tasks.map((task) => [Number(task.page_start || task.details?.source_page_number), task]))
  return <div className="document-page-vision-plan">
    <strong>{plan.converted_to_pdf ? `${String(plan.source_type || '').toUpperCase()} 已在本机转为 PDF` : 'PDF 已在本机逐页渲染'}</strong>
    <small>{plan.page_count || pages.length} 页 · {plan.vision_task_count || pages.length} 个待授权视觉任务 · {plan.render_dpi || '—'} DPI</small>
    <small>{plan.rendered_pdf_file_name ? `本地 PDF：${plan.rendered_pdf_file_name}` : '使用原始 PDF'}</small>
    <p className="vision-export-warning">页面 JPG 已在本机生成且移除 EXIF；只有勾选授权 GPT Vision 后才会外发。</p>
    {pages.length > 0 && <details className="document-page-details"><summary>查看逐页处理计划</summary><div className="document-page-list">{pages.map((page) => {
      const pageNumber = Number(page.source_page_number)
      const task = taskByPage.get(pageNumber)
      return <article key={`${pageNumber}-${page.derivative_sha256 || page.derivative_file_name}`}>
        <span>第 {pageNumber} 页</span>
        <small>{page.derivative_dimensions?.width || '—'} × {page.derivative_dimensions?.height || '—'} · {formatBytes(page.size_bytes)}</small>
        <code title={page.derivative_sha256}>{page.derivative_sha256?.slice(0, 16)}…</code>
        {task ? <StatusBadge status={task.status} /> : <StatusBadge status="created" />}
      </article>
    })}</div></details>}
  </div>
}

const IMPORT_WORKSPACE_STORAGE_KEY = 'design-rag-import-workspace-v1'

function readImportWorkspaceSnapshot() {
  try {
    const raw = sessionStorage.getItem(IMPORT_WORKSPACE_STORAGE_KEY)
    const saved = raw ? JSON.parse(raw) : {}
    return {
      projectName: typeof saved.projectName === 'string' ? saved.projectName : '',
      projectKey: typeof saved.projectKey === 'string' ? saved.projectKey : '',
      forceReprocess: Boolean(saved.forceReprocess),
      fileProcessors: saved.fileProcessors && typeof saved.fileProcessors === 'object' ? saved.fileProcessors : {},
      activeImportTab: saved.activeImportTab === 'batches' ? 'batches' : 'create',
      focusedBatchId: typeof saved.focusedBatchId === 'string' ? saved.focusedBatchId : '',
      batchIds: Array.isArray(saved.batchIds) ? saved.batchIds.filter((value) => typeof value === 'string') : [],
    }
  } catch { return { projectName: '', projectKey: '', forceReprocess: false, fileProcessors: {}, activeImportTab: 'create', focusedBatchId: '', batchIds: [] } }
}

function useImportWorkspace() {
  const initial = useMemo(() => readImportWorkspaceSnapshot(), [])
  const [workspace, setWorkspace] = useState(() => ({
    ...initial, files: [], batches: [], batchApprovals: {}, reviewStates: {}, restored: false,
    refreshRecoveryNote: Boolean(initial.batchIds.length),
  }))
  useEffect(() => {
    let disposed = false
    if (!initial.batchIds.length) {
      setWorkspace((current) => current.restored ? current : { ...current, restored: true })
      return undefined
    }
    Promise.all(initial.batchIds.map((id) => adminApi.getImportBatch(id).catch(() => null))).then((rows) => {
      if (disposed) return
      const batches = rows.filter(Boolean)
      setWorkspace((current) => ({ ...current, batches, restored: true }))
    })
    return () => { disposed = true }
  }, [initial.batchIds])
  useEffect(() => {
    try {
      sessionStorage.setItem(IMPORT_WORKSPACE_STORAGE_KEY, JSON.stringify({
        projectName: workspace.projectName,
        projectKey: workspace.projectKey,
        forceReprocess: workspace.forceReprocess,
        fileProcessors: workspace.fileProcessors,
        activeImportTab: workspace.activeImportTab === 'batches' ? 'batches' : 'create',
        focusedBatchId: workspace.focusedBatchId || '',
        batchIds: workspace.batches.map((batch) => batch.id),
      }))
    } catch { /* Session persistence is best effort only. */ }
  }, [workspace.activeImportTab, workspace.batches, workspace.fileProcessors, workspace.focusedBatchId, workspace.forceReprocess, workspace.projectKey, workspace.projectName])
  return [workspace, setWorkspace]
}

function batchTaskCounts(batch) {
  const tasks = Array.isArray(batch?.tasks) ? batch.tasks : []
  return { markitdown: tasks.filter((task) => task.task_kind === 'markitdown').length, mineru: tasks.filter((task) => task.task_kind === 'mineru').length, vision: tasks.filter((task) => task.task_kind === 'vision').length, hybrid: (batch?.page_reviews || []).filter((review) => review.selected_processor === 'mineru_then_gpt').length }
}

function summarizeManifestTasks(tasks) {
  const countBy = (values) => values.reduce((counts, value) => ({ ...counts, [value]: (counts[value] || 0) + 1 }), {})
  const kindLabels = { mineru: 'MinerU', vision: 'GPT Vision', markitdown: '本地 MarkItDown' }
  const statusLabels = { awaiting_approval: '待授权', scheduled: '已定时', queued: '排队中', running: '处理中', processing: '处理中', submitted: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消', created: '待提交' }
  const format = (counts, labels) => Object.entries(counts).map(([value, count]) => `${labels[value] || value} ${count}`).join(' · ')
  return {
    kinds: format(countBy(tasks.map((task) => task.task_kind || '其他')), kindLabels),
    statuses: format(countBy(tasks.map((task) => task.status || 'created')), statusLabels),
  }
}

function ManifestTaskList({ itemId, tasks, expanded, onToggle }) {
  const summary = summarizeManifestTasks(tasks)
  return <div className="manifest-tasks">
    <div className="manifest-task-summary">
      <strong>{tasks.length} 个任务</strong>
      <small title={summary.kinds}>{summary.kinds}</small>
      <small title={summary.statuses}>{summary.statuses}</small>
      <button type="button" className="inline-action-button" aria-expanded={expanded} onClick={() => onToggle(itemId)}>{expanded ? '隐藏任务' : `显示 ${tasks.length} 个任务`}</button>
    </div>
    {expanded && <div className="manifest-task-list" aria-label={`${tasks.length} 个外部任务`}>
      {tasks.map((task) => <div className="manifest-task-row" key={task.id}>
        <strong>{task.task_kind === 'vision' ? 'GPT Vision' : task.task_kind === 'markitdown' ? '本地 MarkItDown' : 'MinerU'}</strong>
        <small>{task.task_kind === 'markitdown' ? '本机执行，不外发' : task.external_target} · <StatusBadge status={task.status} /></small>
      </div>)}
    </div>}
  </div>
}

function ImportBatchCard({ batch, approvals, reviewState, busy, onRefresh, onUpdate, onApprove, onReviewState, onError }) {
  const [expanded, setExpanded] = useState(true)
  const [expandedTaskItems, setExpandedTaskItems] = useState({})
  const [reroutingItemId, setReroutingItemId] = useState('')
  const [retryingItemId, setRetryingItemId] = useState('')
  const persistReviewState = useCallback((state) => onReviewState?.(batch.id, state), [batch.id, onReviewState])
  const items = Array.isArray(batch.items) ? batch.items : []
  const tasks = Array.isArray(batch.tasks) ? batch.tasks : []
  const pageReviews = Array.isArray(batch.page_reviews) ? batch.page_reviews : []
  const counts = batchTaskCounts(batch)
  const duplicateItems = items.filter((item) => item.outcome === 'duplicate_skipped')
  const approveMineru = Boolean(approvals?.mineru)
  const approveVision = Boolean(approvals?.vision)
  const scheduleMode = approvals?.scheduleMode || 'immediate'
  const scheduledFor = approvals?.scheduledFor ? dayjs(approvals.scheduledFor) : null
  const customTimeValid = scheduleMode !== 'custom' || Boolean(scheduledFor?.isAfter(dayjs()))
  const canApprove = batch.status === 'awaiting_approval' && (counts.mineru || counts.vision)
  const taskListExpanded = (itemId, itemTasks) => {
    if (Object.prototype.hasOwnProperty.call(expandedTaskItems, itemId)) return Boolean(expandedTaskItems[itemId])
    return itemTasks.length <= 3 || itemTasks.some((task) => task.status === 'failed' || task.visual_analysis_status === 'failed' || task.ingestion_status === 'failed')
  }
  const toggleTaskList = (itemId) => setExpandedTaskItems((current) => ({
    ...current,
    [itemId]: !taskListExpanded(itemId, tasks.filter((task) => task.item_id === itemId)),
  }))
  const buttonLabel = scheduleMode === 'nightly'
    ? '确认授权并安排夜间解析'
    : scheduleMode === 'custom' ? '确认授权并安排定时解析' : '确认授权并立即加入队列'
  const scheduledMessage = batch.status === 'scheduled' || batch.status === 'scheduling'
    ? `${batch.schedule_mode === 'nightly' ? '夜间解析' : '定时解析'}已安排于 ${formatDate(batch.scheduled_for)} 开始。到点前不会向 MinerU 或 GPT Vision 发送文件。`
    : ''
  async function rerouteToMineru(itemId) {
    setReroutingItemId(itemId)
    try { onUpdate(await adminApi.rerouteImportBatchItemToMineru(batch.id, itemId)) }
    catch (operationError) { onError?.(operationError.message || '无法改用 MinerU') }
    finally { setReroutingItemId('') }
  }
  async function retryMarkitdown(itemId) {
    setRetryingItemId(itemId)
    try { onUpdate(await adminApi.retryImportBatchMarkitdown(batch.id, itemId)) }
    catch (operationError) { onError?.(operationError.message || '无法重试本地 MarkItDown') }
    finally { setRetryingItemId('') }
  }

  return <article id={`import-batch-${batch.id}`} className="panel import-batch-card">
    <header className="import-batch-card-header">
      <div><p className="eyebrow">IMPORT BATCH</p><h2>{batch.project_name || '项目导入批次'}</h2><p className="muted"><code>{batch.id}</code> · {formatDate(batch.created_at)}</p></div>
      <div className="import-batch-header-actions"><StatusBadge status={batch.status} /><button type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? '收起' : '展开'}</button><button type="button" onClick={() => onRefresh(batch.id)} disabled={busy}>刷新此批次</button></div>
    </header>
    {expanded && <>
      {batch.error_message && <section className="error-banner"><strong>批次提示：</strong>{batch.error_message}</section>}
      {scheduledMessage && <section className="batch-scheduled-notice"><strong>{batch.status === 'scheduling' ? '正在按计划进入队列' : '解析已安排'}</strong><span>{scheduledMessage}</span></section>}
      <div className="manifest-summary"><article><strong>{items.length}</strong><span>原始文件</span></article><article><strong>{duplicateItems.length}</strong><span>重复跳过</span></article><article><strong>{counts.markitdown}</strong><span>本地 MarkItDown</span></article><article><strong>{counts.mineru}</strong><span>MinerU 任务</span></article><article><strong>{counts.vision}</strong><span>GPT Vision 任务</span></article><article><strong>{counts.hybrid}</strong><span>MinerU + GPT 页</span></article></div>
      {pageReviews.length > 0 && <PdfPageReview batch={batch} disabled={busy} reviewState={reviewState} onReviewStateChange={persistReviewState} onBatchUpdated={onUpdate} onError={onError} />}
      <div className="manifest-table" role="region" aria-label="批次文件和处理计划"><div className="manifest-row manifest-head"><span>原始文件 / SHA-256</span><span>本地处理结果</span><span>外部任务</span></div>{items.map((item) => { const itemTasks = tasks.filter((task) => task.item_id === item.id); const localTask = itemTasks.find((task) => task.task_kind === 'markitdown'); const localResult = localTask?.ingestion_provider_response || {}; const hasReview = pageReviews.some((review) => review.item_id === item.id); return <div className="manifest-row" key={item.id}><div className="manifest-source"><strong title={item.source_relative_path}>{item.source_relative_path}</strong><small>{item.source_file_type?.toUpperCase()} · {formatBytes(item.source_size_bytes)}</small><small>{item.resolved_processor === 'markitdown' ? '本地 MarkItDown · 无需授权' : item.resolved_processor === 'mineru' ? 'MinerU · 需要明确授权' : '自动选择'}</small>{item.locator_type && <small>引用定位：{item.locator_type === 'word_semantic' ? '章节 / 段落 / 表格' : item.locator_type === 'excel_cell_range' ? '工作表 / 单元格范围' : 'Markdown 内容块'}</small>}<code title={item.source_sha256}>{item.source_sha256}</code></div><div className="manifest-derived">{item.outcome === 'duplicate_skipped' ? <><StatusBadge status="skipped" /><small>复用已有导入记录：{item.existing_ingestion_job_id}</small></> : hasReview ? <><StatusBadge status={batch.status === 'awaiting_page_review' ? 'awaiting_page_review' : 'awaiting_approval'} /><small>PDF 共 {pageReviews.filter((review) => review.item_id === item.id).length} 页，已按页保存本地预览与处理方案。</small></> : <><StatusBadge status={localTask?.status || (item.outcome === 'failed' ? 'failed' : 'created')} /><small>{item.error_message || localTask?.ingestion_error_message || item.processing_plan?.route_reason || '等待处理'}</small>{localTask?.status === 'succeeded' && <><small>{localResult.chunk_count || 0} 个结构化章节 · {localResult.image_asset_count || 0} 张嵌入图片</small><a className="inline-action-button" href={adminApi.importBatchMarkitdownUrl(batch.id, item.id)} target="_blank" rel="noreferrer">查看 Markdown</a></>}{item.resolved_processor === 'markitdown' && localTask?.status === 'failed' && <span className="inline-action-group"><button type="button" className="inline-action-button" disabled={busy || retryingItemId === item.id} onClick={() => retryMarkitdown(item.id)}>{retryingItemId === item.id ? '正在重试…' : '重试本地解析'}</button><button type="button" className="inline-action-button" disabled={busy || reroutingItemId === item.id} onClick={() => rerouteToMineru(item.id)}>{reroutingItemId === item.id ? '正在改用…' : '改用 MinerU'}</button></span>}</>}</div>{itemTasks.length ? <ManifestTaskList itemId={item.id} tasks={itemTasks} expanded={taskListExpanded(item.id, itemTasks)} onToggle={toggleTaskList} /> : <div className="manifest-tasks"><small>{hasReview && batch.status === 'awaiting_page_review' ? '等待人工确认分页方案' : '无解析任务'}</small></div>}</div>})}</div>
      {batch.status === 'awaiting_page_review' && <section className="batch-awaiting-review"><strong>请先完成上方 PDF 分页审核。</strong><span>审核未确认前，MinerU 与 GPT Vision 都不会进入外部队列。</span></section>}
      {canApprove && <section className="batch-approval">
        <div><p className="eyebrow">EXPLICIT EXPORT APPROVAL</p><h3>确认授权与解析时间</h3><p className="muted">授权内容与当前 Manifest SHA-256 绑定。定时解析只延后本次已授权任务，绝不会自动发现或外发新文件。</p></div>
        {counts.hybrid > 0 && <p className="local-only-hint">其中 {counts.hybrid} 页采用“MinerU 提取 + GPT 识图”：MinerU 完成后才会生成 GPT 图片任务。</p>}
        {counts.mineru > 0 && <label className="approval-check"><input type="checkbox" checked={approveMineru} onChange={(event) => onApprove(batch.id, { mineru: event.target.checked })} disabled={busy} />我明确同意将此批次的 {counts.mineru} 个 MinerU 任务发送到已配置的 MinerU 服务。</label>}
        {counts.hybrid > 0 && <label className="approval-check"><input type="checkbox" checked={approveVision} onChange={(event) => onApprove(batch.id, { vision: event.target.checked })} disabled={busy} />我明确同意在 MinerU 提取图片后，将其中 {counts.hybrid} 页提取出的图片发送到已配置的 GPT Vision 服务。</label>}
        {counts.vision > 0 && <label className="approval-check"><input type="checkbox" checked={approveVision} onChange={(event) => onApprove(batch.id, { vision: event.target.checked })} disabled={busy} />我明确同意将此批次的 {counts.vision} 个本地派生 JPG 发送到已配置的 GPT Vision 服务。</label>}
        <div className="parse-schedule-control">
          <strong>选择处理方式</strong>
          <Radio.Group value={scheduleMode} onChange={(event) => onApprove(batch.id, { scheduleMode: event.target.value })} disabled={busy} optionType="button" buttonStyle="solid">
            <Radio.Button value="immediate">立刻解析</Radio.Button>
            <Radio.Button value="nightly">夜间解析</Radio.Button>
            <Radio.Button value="custom">自定义时间</Radio.Button>
          </Radio.Group>
          {scheduleMode === 'immediate' && <small>确认后立即加入已授权的 MinerU / GPT Vision 队列。</small>}
          {scheduleMode === 'nightly' && <small>服务端会按上海时间安排在下一次 19:00 开始解析；到点前文件仅保留在本地。</small>}
          {scheduleMode === 'custom' && <div className="parse-schedule-custom"><DatePicker value={scheduledFor} showTime={{ format: 'HH:mm', minuteStep: 5, hideDisabledOptions: true }} format="YYYY-MM-DD HH:mm" placeholder="选择未来解析时间" disabled={busy} disabledDate={(current) => current && current < dayjs().startOf('day')} getPopupContainer={() => document.body} classNames={{ popup: { root: 'parse-schedule-picker' } }} placement="bottomLeft" disabledTime={disabledScheduleTime} onChange={(value) => onApprove(batch.id, { scheduledFor: value?.toISOString() || null })} /><small>{customTimeValid ? '请选择未来时间；服务端会以该确切时间执行。' : '请选择晚于当前时间的日期和时间。'}</small></div>}
        </div>
        <button className="primary-button" onClick={() => onApprove(batch.id)} disabled={busy || (!approveMineru && !approveVision) || !customTimeValid}>{busy ? '正在保存授权…' : buttonLabel}</button>
      </section>}
    </>}
  </article>
}

function ImportPage({ onImported, workspace, setWorkspace }) {
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [autofillMessage, setAutofillMessage] = useState('')
  const inputRef = useRef(null)
  const folderInputRef = useRef(null)
  const files = workspace.files || []
  const fileProcessors = workspace.fileProcessors || {}
  const batches = workspace.batches || []
  const fileTree = useMemo(() => buildFileTree(files), [files])
  const updateWorkspace = useCallback((updater) => setWorkspace((current) => typeof updater === 'function' ? updater(current) : { ...current, ...updater }), [setWorkspace])
  const updateBatch = useCallback((batch) => updateWorkspace((current) => ({ ...current, batches: current.batches.map((item) => item.id === batch.id ? batch : item) })), [updateWorkspace])
  const refreshBatch = useCallback(async (batchId) => {
    try { updateBatch(await adminApi.getImportBatch(batchId)) }
    catch (operationError) { setError(operationError.message || '无法刷新批次') }
  }, [updateBatch])

  useEffect(() => {
    const active = batches.filter((batch) => ['uploading', 'preprocessing', 'awaiting_page_review', 'scheduled', 'scheduling', 'queued', 'processing'].includes(batch.status))
    if (!active.length) return undefined
    const sources = active.map((batch) => {
      const source = new EventSource(adminApi.importBatchEventsUrl(batch.id))
      const receive = (event) => { try { const payload = JSON.parse(event.data); if (payload?.id) updateBatch(payload) } catch { /* The next event or manual refresh will recover. */ } }
      source.addEventListener('batch', receive)
      source.addEventListener('complete', receive)
      return source
    })
    return () => sources.forEach((source) => source.close())
  }, [batches.map((batch) => `${batch.id}:${batch.status}`).join('|'), updateBatch])

  function updateDraft(values) { updateWorkspace(values) }
  function addFiles(next) {
    const suggestion = inferProjectDefaults(next)
    if (suggestion) {
      updateWorkspace((current) => ({ ...current, projectName: current.projectName.trim() ? current.projectName : suggestion.projectName, projectKey: current.projectKey.trim() ? current.projectKey : suggestion.projectExternalKey }))
      setAutofillMessage(`已识别项目文件夹“${suggestion.projectName}”；项目名称和项目标识在为空时已自动填写，仍可手动修改。`)
    }
    updateWorkspace((current) => {
      const map = new Map((current.files || []).map((file) => [fileDraftKey(file), file]))
      next.forEach((file) => map.set(fileDraftKey(file), file))
      return { ...current, files: [...map.values()] }
    })
  }
  function updateFileProcessor(fileKey, processor) {
    updateWorkspace((current) => ({ ...current, fileProcessors: { ...current.fileProcessors, [fileKey]: processor } }))
  }
  async function handleDrop(event) { event.preventDefault(); setDragging(false); setError(''); try { const dropped = await collectDroppedFiles(event.dataTransfer); if (!dropped.length) throw new Error('没有在拖入内容中找到可导入的文件。'); addFiles(dropped) } catch (dropError) { setError(dropError.message || '无法读取拖入的文件夹，请改为选择其中的文件。') } }
  async function startImport() {
    if (!workspace.projectName.trim() || !workspace.projectKey.trim() || !files.length) { setError('请填写项目名称、项目标识，并至少选择一个文件。'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const created = await adminApi.createImportBatch({ projectName: workspace.projectName.trim(), projectExternalKey: workspace.projectKey.trim(), forceReprocess: workspace.forceReprocess })
      updateWorkspace((current) => ({ ...current, batches: [...current.batches, created], activeImportTab: 'batches', focusedBatchId: created.id }))
      for (const file of files) await adminApi.uploadImportBatchFile({ batchId: created.id, file, processorSelection: fileProcessors[fileDraftKey(file)] || 'auto' })
      const prepared = await adminApi.prepareImportBatch(created.id)
      updateWorkspace((current) => ({ ...current, batches: current.batches.map((batch) => batch.id === created.id ? prepared : batch), files: [], fileProcessors: {}, forceReprocess: false, batchApprovals: { ...current.batchApprovals, [created.id]: { mineru: false, vision: false } } }))
      const taskCounts = prepared.manifest?.task_counts || {}
      setNotice(prepared.status === 'awaiting_page_review' ? `已本地归档并生成 ${prepared.page_reviews?.length || 0} 页 PDF 预览。请在下方逐页确认 MinerU 或 GPT Vision 后，再进行外部授权。` : `已本地归档并生成不可变 Manifest：${prepared.items?.length || files.length} 个原文件，${taskCounts.markitdown || 0} 个本地 MarkItDown 任务，${taskCounts.mineru || 0} 个 MinerU 任务，${taskCounts.vision || 0} 个 GPT Vision 任务。本地任务无需授权并会立即执行。`)
      onImported()
    } catch (operationError) { setError(operationError.message || '项目批次导入失败') }
    finally { setBusy(false) }
  }
  async function approveBatch(batchId, change) {
    if (change) { updateWorkspace((current) => ({ ...current, batchApprovals: { ...current.batchApprovals, [batchId]: { ...(current.batchApprovals[batchId] || {}), ...change } } })); return }
    const approval = workspace.batchApprovals?.[batchId] || {}
    const batch = batches.find((item) => item.id === batchId)
    const scheduleMode = approval.scheduleMode || 'immediate'
    const scheduledFor = approval.scheduledFor || null
    if (!batch?.manifest_sha256 || (!approval.mineru && !approval.vision)) { setError('没有选择任何外部处理授权；当前批次仍将只保留在本地。'); return }
    if (scheduleMode === 'custom' && (!scheduledFor || !dayjs(scheduledFor).isAfter(dayjs()))) { setError('请选择晚于当前时间的自定义解析时间。'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const approved = await adminApi.approveImportBatch({ batchId, manifestSha256: batch.manifest_sha256, approveMineru: Boolean(approval.mineru), approveVision: Boolean(approval.vision), scheduleMode, scheduledFor: scheduleMode === 'custom' ? scheduledFor : null })
      updateBatch(approved)
      setNotice(scheduleMode === 'immediate' ? '已按当前 Manifest 授权并加入对应后台队列。页面会保留在导入工作区，并通过局部状态更新显示进度。' : '已保存本次明确授权的解析排程。到点前不会向 MinerU 或 GPT Vision 发送文件。')
      onImported()
    } catch (operationError) { setError(operationError.message || '批次授权失败') }
    finally { setBusy(false) }
  }

  const persistReviewState = useCallback((batchId, state) => {
    setWorkspace((current) => {
      const previous = current.reviewStates?.[batchId]
      const same = previous
        && previous.activeItemId === state.activeItemId
        && previous.activePageId === state.activePageId
        && previous.zoom === state.zoom
        && previous.zoomMode === state.zoomMode
        && previous.rotation === state.rotation
        && JSON.stringify(previous.selectedIds || []) === JSON.stringify(state.selectedIds || [])
      if (same) return current
      return { ...current, reviewStates: { ...current.reviewStates, [batchId]: state } }
    })
  }, [setWorkspace])

  const batchCount = batches.length
  const activeImportTab = workspace.activeImportTab === 'batches' && batchCount ? 'batches' : 'create'
  const selectImportTab = useCallback((key) => {
    if (key === 'batches' && !batchCount) return
    updateWorkspace((current) => ({ ...current, activeImportTab: key, focusedBatchId: key === 'batches' ? current.focusedBatchId : '' }))
  }, [batchCount, updateWorkspace])

  useEffect(() => {
    if (workspace.activeImportTab === 'batches' && !batchCount) {
      updateWorkspace((current) => ({ ...current, activeImportTab: 'create', focusedBatchId: '' }))
    }
  }, [batchCount, updateWorkspace, workspace.activeImportTab])

  useEffect(() => {
    if (activeImportTab !== 'batches' || !workspace.focusedBatchId) return undefined
    const batchId = workspace.focusedBatchId
    const timer = window.setTimeout(() => {
      document.getElementById(`import-batch-${batchId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      updateWorkspace((current) => current.focusedBatchId === batchId ? { ...current, focusedBatchId: '' } : current)
    }, 80)
    return () => window.clearTimeout(timer)
  }, [activeImportTab, updateWorkspace, workspace.focusedBatchId])

  const draftPanel = <>
    <section className="panel import-panel"><div className="import-form"><label>项目名称<input value={workspace.projectName} onChange={(e) => updateDraft({ projectName: e.target.value })} placeholder="例如：02 华润顶层会所" disabled={busy} /></label><label>项目标识<input value={workspace.projectKey} onChange={(e) => updateDraft({ projectKey: e.target.value })} placeholder="例如：crland-top-clubhouse" disabled={busy} /></label><label className="force-reprocess-check"><input type="checkbox" checked={workspace.forceReprocess} onChange={(event) => updateDraft({ forceReprocess: event.target.checked })} disabled={busy} /><span><strong>重新解析相同文件</strong><small>即使 SHA-256 已存在，也会保留历史记录并创建新的可审计任务；可能产生新的外部解析费用。</small></span></label><div className={`drop-zone ${dragging ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={handleDrop} onClick={() => inputRef.current?.click()}><input ref={inputRef} type="file" multiple hidden onChange={(e) => { setError(''); addFiles([...e.target.files]); e.target.value = '' }} /><input ref={folderInputRef} type="file" webkitdirectory="" directory="" multiple hidden onChange={(e) => { setError(''); addFiles([...e.target.files]); e.target.value = '' }} /><span className="drop-icon">↥</span><strong>拖拽文件或项目文件夹到这里</strong><span>PDF 会先本地渲染为逐页预览，未确认前绝不发送到 MinerU 或 GPT。</span><button type="button" className="select-folder-button" onClick={(e) => { e.stopPropagation(); folderInputRef.current?.click() }} disabled={busy}>选择项目文件夹</button><small>支持 PDF、JPG、PNG、WEBP、PPTX、DOCX 等格式</small></div><div className="import-actions"><button className="primary-button" onClick={startImport} disabled={busy || !files.length}>{busy ? '正在本地归档与预处理…' : '创建新的本地导入批次'}</button><span className="local-only-hint">创建成功后会自动打开“查看导入批次”；上方表单只用于下一次导入。</span></div></div></section>
    {files.length > 0 && <section className="panel imported-file-tree"><div className="panel-heading"><div><p className="eyebrow">NEXT BATCH DRAFT</p><h2>下一批待导入文件</h2><p className="muted">这些浏览器文件在切换 Admin 菜单期间保留；刷新浏览器后需要重新选择。</p></div><span>{files.length} 个文件</span></div><FileTree node={fileTree} processorSelections={fileProcessors} disabled={busy} onProcessorChange={updateFileProcessor} /></section>}
  </>

  const batchesPanel = <section className="import-workspace-batches"><div className="panel-heading"><div><p className="eyebrow">THIS SESSION</p><h2>本次导入批次</h2><p className="muted">每个批次独立保留文件、PDF 审核方案、授权状态与后台进度；运行中仅局部更新，不刷新整个页面。</p></div><span>{batchCount} 个批次</span></div>{!workspace.restored && <p className="muted">正在恢复本次会话批次…</p>}{batches.length ? batches.map((batch) => <ImportBatchCard key={batch.id} batch={batch} busy={busy} approvals={workspace.batchApprovals?.[batch.id]} reviewState={workspace.reviewStates?.[batch.id]} onRefresh={refreshBatch} onUpdate={updateBatch} onApprove={approveBatch} onReviewState={persistReviewState} onError={setError} />) : <section className="panel empty-import-batches"><strong>还没有创建导入批次</strong><span>选择文件并创建后，批次会显示在这里，不会覆盖之前的批次。</span></section>}</section>

  return <section className="content-page import-workspace-page">
    <header className="page-header"><div><p className="eyebrow">PERSISTENT BATCH KNOWLEDGE INGESTION</p><h1>批次导入知识库</h1><p className="subtitle">PDF 先在本地分页并逐页人工审核；切换 Admin 页面后，草稿、批次和审核位置都会保留。</p></div></header>
    {workspace.refreshRecoveryNote && <section className="notice-banner">已恢复本次会话的导入批次。浏览器刷新后无法恢复尚未上传的本地文件，请重新选择文件即可继续创建新批次。</section>}
    {autofillMessage && <p className="auto-project-hint">{autofillMessage}</p>}{notice && <section className="notice-banner">{notice}</section>}{error && <section className="error-banner"><strong>操作未完成：</strong>{error}</section>}
    <Tabs className="import-workspace-tabs" activeKey={activeImportTab} onChange={selectImportTab} destroyOnHidden={false} items={[
      { key: 'create', label: '批次导入知识库', children: draftPanel },
      { key: 'batches', label: `查看导入批次（${batchCount}）`, disabled: !batchCount, children: batchesPanel },
    ]} />
  </section>
}

function isSupersededImportRecord(record) {
  return record?.record_disposition === 'superseded_duplicate'
}

function IngestionStatusBadge({ status, record }) {
  if (isSupersededImportRecord(record)) {
    return <span className="badge status-skipped">重复任务 · 已由成功结果替代</span>
  }
  const finalStatus = record?.final_status
  const label = record?.final_status_label || ({
    materialized: '已写入知识库',
    pending: '待提交',
    created: '待提交',
    queued: '排队中',
    processing: '导入处理中',
    submitted: '导入处理中',
    succeeded: '已写入知识库',
    failed: '导入失败',
    cancelled: '已取消',
  }[status] || status)
  const state = finalStatus === 'materialized' ? 'succeeded' : (finalStatus || status)
  return <span className={`badge status-${state}`}>{label}</span>
}

function isImageImport(record) {
  return ['jpg', 'jpeg', 'png', 'webp'].includes(String(record?.file_type || '').replace('.', '').toLowerCase())
}

function importBatchGroupKey(record) {
  if (record.provider_batch_id) return `provider:${record.provider_batch_id}`
  const created = String(record.created_at || '').slice(0, 16).replace(/[:T]/g, '-')
  return `history:${created || record.id}`
}

function buildImportRecordTree(records) {
  const projects = new Map()
  for (const record of records) {
    const projectId = String(record.project_id || 'unknown-project')
    if (!projects.has(projectId)) projects.set(projectId, { id: projectId, name: record.project_name || '未命名项目', records: [], batches: new Map() })
    const project = projects.get(projectId)
    project.records.push(record)
    const batchKey = importBatchGroupKey(record)
    if (!project.batches.has(batchKey)) project.batches.set(batchKey, { key: batchKey, records: [], importedAt: record.created_at, archivedAt: record.archived_at || record.materialized_at || record.completed_at })
    const batch = project.batches.get(batchKey)
    batch.records.push(record)
    if (String(record.created_at || '') > String(batch.importedAt || '')) batch.importedAt = record.created_at
    const archivedAt = record.archived_at || record.materialized_at || record.completed_at
    if (archivedAt && String(archivedAt) > String(batch.archivedAt || '')) batch.archivedAt = archivedAt
  }
  return [...projects.values()].map((project) => ({
    ...project,
    latestImportedAt: project.records.reduce((latest, item) => String(item.created_at || '') > String(latest || '') ? item.created_at : latest, null),
    latestArchivedAt: project.records.reduce((latest, item) => { const value = item.archived_at || item.materialized_at || item.completed_at; return value && String(value) > String(latest || '') ? value : latest }, null),
    batches: [...project.batches.values()].sort((a, b) => String(b.importedAt || '').localeCompare(String(a.importedAt || ''))),
  })).sort((a, b) => String(b.latestImportedAt || '').localeCompare(String(a.latestImportedAt || '')))
}

const ImportRecordRow = memo(function ImportRecordRow({ record, recordStore, selected, disabled, onToggle, onOpen }) {
  const subscribe = useCallback((listener) => recordStore.subscribe(record.id, listener), [record.id, recordStore])
  const getSnapshot = useCallback(() => recordStore.get(record.id, record), [record, recordStore])
  const liveRecord = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const superseded = isSupersededImportRecord(liveRecord)
  const supersededCount = Number(liveRecord.superseded_attempt_count || 0)
  const activeSupersededCount = Number(liveRecord.active_superseded_attempt_count || 0)
  const sourceDocumentName = String(liveRecord.source_document_name || '').trim()
  return <article className={`import-record-row ${selected ? 'is-selected' : ''}${superseded ? ' is-superseded' : ''}`}>
    <label className="record-selection" onClick={(event) => event.stopPropagation()}>
      <input type="checkbox" checked={selected} onChange={() => onToggle(liveRecord.id)} aria-label={`选择 ${liveRecord.source_file_name || liveRecord.file_name}`} disabled={disabled || superseded} />
    </label>
    <button className="record-open-button" type="button" onClick={() => onOpen(liveRecord.id)}>
      <span className="record-file-icon">{isImageImport(liveRecord) ? '▧' : '▤'}</span>
      <span className="record-main"><strong>{liveRecord.source_file_name || liveRecord.file_name}</strong><small>{liveRecord.project_name} · {liveRecord.file_type?.toUpperCase() || 'FILE'} · {formatBytes(liveRecord.source_size_bytes)}</small>{sourceDocumentName ? <small className="record-source-document" title={`由 ${sourceDocumentName} 分页生成`}>分页来源：{sourceDocumentName}</small> : null}</span>
      <span className="record-state">
        <IngestionStatusBadge status={liveRecord.status} record={liveRecord} />
        {superseded ? <small>仅保留作审计查看，不可再次提交或删除。</small> : <>
          {liveRecord.visual_analysis_status && <small>图片分析：<StatusBadge status={liveRecord.visual_analysis_status} /></small>}
          {supersededCount > 0 && <small>已替代 {supersededCount} 条重复任务{activeSupersededCount > 0 ? `（其中 ${activeSupersededCount} 条仍在运行）` : ''}</small>}
        </>}
      </span>
      <span className="record-time">{formatDate(liveRecord.updated_at || liveRecord.created_at)}</span>
      <span className="record-arrow">›</span>
    </button>
  </article>
})

function rememberVisualJobIds(jobs) {
  const current = JSON.parse(localStorage.getItem('design-rag-visual-job-ids') || '[]')
  const ids = [...new Set([...current, ...jobs.map((job) => job.id).filter(Boolean)])]
  localStorage.setItem('design-rag-visual-job-ids', JSON.stringify(ids))
}

function batchTaskStatus(task) {
  return task?.status || task?.visual_analysis_status || task?.ingestion_status || 'awaiting_approval'
}

function batchTaskSourceName(task) {
  const details = task?.details && typeof task.details === 'object' ? task.details : {}
  return details.source_document_name || task?.source_file_name || task?.file_name || '未命名文件'
}

function batchTaskPageLabel(task) {
  if (task?.page_start && task?.page_end && task.page_start !== task.page_end) return `第 ${task.page_start}–${task.page_end} 页`
  if (task?.page_start) return `第 ${task.page_start} 页`
  const details = task?.details && typeof task.details === 'object' ? task.details : {}
  if (details.source_image_index) return `第 ${details.source_image_index} 张提取图片`
  return '原始文件'
}

function batchTaskError(task) {
  const details = task?.details && typeof task.details === 'object' ? task.details : {}
  return task?.visual_analysis_error_code || task?.ingestion_error_code || details.queue_error || details.queue_result?.reason || ''
}

function BatchImportFilesPanel({ refreshKey }) {
  const [batches, setBatches] = useState([])
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const [batch, setBatch] = useState(null)
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState('')

  const loadBatches = useCallback(async () => {
    setLoadingBatches(true)
    setError('')
    try {
      const payload = await adminApi.listImportBatches({ limit: 50 })
      const items = Array.isArray(payload.items) ? payload.items : []
      setBatches(items)
      setSelectedBatchId((current) => current && items.some((item) => item.id === current) ? current : (items[0]?.id || ''))
    } catch (loadError) {
      setError(loadError.message || '无法读取批次导入记录')
    } finally {
      setLoadingBatches(false)
    }
  }, [])

  const loadBatchDetail = useCallback(async () => {
    if (!selectedBatchId) { setBatch(null); return }
    setLoadingDetail(true)
    try {
      setBatch(await adminApi.getImportBatch(selectedBatchId))
    } catch (loadError) {
      setError(loadError.message || '无法读取该批次的文件明细')
    } finally {
      setLoadingDetail(false)
    }
  }, [selectedBatchId])

  useEffect(() => { loadBatches() }, [loadBatches, refreshKey])
  useEffect(() => { loadBatchDetail() }, [loadBatchDetail])

  const tasksByItem = useMemo(() => {
    const groups = new Map()
    for (const task of Array.isArray(batch?.tasks) ? batch.tasks : []) {
      const current = groups.get(task.item_id) || []
      current.push(task)
      groups.set(task.item_id, current)
    }
    return groups
  }, [batch])

  return <section className="panel batch-file-records">
    <div className="panel-heading batch-file-records-heading">
      <div><p className="eyebrow">BATCH FILE INVENTORY</p><h2>批次导入文件明细</h2><p className="muted">这里按原始文件显示处理路由、导入状态和所有拆分出的页面/图片任务；不会再只显示浏览器本机缓存过的记录。</p></div>
      <button className="secondary-button" onClick={loadBatches} disabled={loadingBatches || loadingDetail}>刷新批次</button>
    </div>
    {error && <section className="error-banner"><strong>文件明细读取失败：</strong>{error}</section>}
    {loadingBatches ? <p className="empty">正在读取批次与文件明细…</p> : !batches.length ? <p className="empty">尚无批次导入记录。</p> : <>
      <label className="batch-picker">导入批次<select value={selectedBatchId} onChange={(event) => setSelectedBatchId(event.target.value)}>{batches.map((item) => <option key={item.id} value={item.id}>{item.project_name} · {formatDate(item.updated_at)} · {item.status}</option>)}</select></label>
      {loadingDetail ? <p className="empty">正在读取该批次的每个文件…</p> : batch && <>
        <div className="batch-file-summary"><article><strong>{batch.items?.length || 0}</strong><span>原始文件</span></article><article><strong>{batch.tasks?.length || 0}</strong><span>处理任务 / 页面</span></article><article><strong>{batch.project_name}</strong><span>所属项目</span></article><article><StatusBadge status={batch.status} /><span>批次状态</span></article></div>
        <div className="batch-file-list">{(batch.items || []).map((item) => {
          const tasks = tasksByItem.get(item.id) || []
          const plan = item.processing_plan && typeof item.processing_plan === 'object' ? item.processing_plan : {}
          return <article className="batch-file-row" key={item.id}>
            <div className="batch-file-row-main"><span className="record-file-icon">{isImageImport({ file_type: item.source_file_type }) ? '▧' : '▤'}</span><div><strong title={item.source_relative_path}>{item.source_file_name || item.source_relative_path}</strong><small title={item.source_relative_path}>{item.source_relative_path}</small><small>{item.source_file_type?.toUpperCase() || 'FILE'} · {formatBytes(item.source_size_bytes)} · {formatDate(item.updated_at || item.created_at)}</small></div></div>
            <div className="batch-file-route"><strong>{plan.processing_route || '本地归档'}</strong><small>{plan.route_reason || plan.reprocess_reason || '等待生成处理计划'}</small></div>
            <div className="batch-file-outcome"><StatusBadge status={item.outcome || 'created'} /><small>{tasks.length ? `${tasks.length} 个子任务` : '尚未拆分子任务'}</small></div>
            {item.error_message && <p className="batch-file-error">{item.error_message}</p>}
            {tasks.length > 0 && <details className="batch-task-details" open={tasks.length <= 8}><summary>查看 {tasks.length} 个处理任务</summary><div className="batch-task-list">{tasks.map((task) => <article className="batch-task-row" key={task.id}><div><strong>{batchTaskPageLabel(task)} · {task.task_kind === 'vision' ? '视觉分析' : (task.details?.pipeline === 'mineru_then_gpt' ? 'MinerU 提取（后续 GPT 识图）' : '文档解析')}</strong><small>{task.file_name || batchTaskSourceName(task)} · {formatBytes(task.source_size_bytes)} · {task.external_target || '本地'}</small></div><div><StatusBadge status={batchTaskStatus(task)} />{batchTaskError(task) && <small className="batch-file-error">{batchTaskError(task)}</small>}</div></article>)}</div></details>}
          </article>
        })}</div>
      </>}
    </>}
  </section>
}

function BatchImportRecordsPage({ refreshKey }) {
  return <section className="content-page"><BatchImportFilesPanel refreshKey={refreshKey} /></section>
}



function ImportRecordsPage({ projects, refreshKey, onImported, setActivePage }) {
  const [records, setRecords] = useState([])
  const [total, setTotal] = useState(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [projectFilter, setProjectFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const recordsRef = useRef([])
  const recordStoreRef = useRef(null)
  if (!recordStoreRef.current) recordStoreRef.current = createImportRecordStore()
  const [selected, setSelected] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [operationBusy, setOperationBusy] = useState(false)
  const [approveExternal, setApproveExternal] = useState(false)
  const [selectedJobIds, setSelectedJobIds] = useState([])
  const [approveBatchExternal, setApproveBatchExternal] = useState(false)
  const [queueInfo, setQueueInfo] = useState(null)
  const [queueConfirm, setQueueConfirm] = useState(false)
  const [showSuperseded, setShowSuperseded] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await adminApi.listIngestionJobs({ limit: 100, status: statusFilter, project_id: projectFilter, include_superseded: showSuperseded })
      const nextRecords = Array.isArray(payload.items) ? payload.items : []
      recordsRef.current = nextRecords
      recordStoreRef.current.replace(nextRecords)
      setRecords(nextRecords)
      setTotal(Number(payload.total || 0))
    } catch (loadError) {
      setError(loadError.message || '无法读取导入记录')
    } finally {
      setLoading(false)
    }
  }, [projectFilter, showSuperseded, statusFilter])

  useEffect(() => { load() }, [load, refreshKey])
  useEffect(() => {
    let disposed = false
    adminApi.getVisualQueueInfo().then((info) => { if (!disposed) setQueueInfo(info) }).catch(() => { if (!disposed) setQueueInfo(null) })
    return () => { disposed = true }
  }, [])
  useEffect(() => {
    const available = new Set(records.filter((record) => !isSupersededImportRecord(record)).map((record) => record.id))
    setSelectedJobIds((current) => {
      const next = current.filter((id) => available.has(id))
      return next.length === current.length ? current : next
    })
  }, [records])

  const selectedRecords = useMemo(() => {
    const latestById = new Map(recordsRef.current.map((record) => [record.id, record]))
    return selectedJobIds.map((id) => latestById.get(id)).filter(Boolean)
  }, [records, selectedJobIds])
  const selectedDocuments = selectedRecords.filter((record) => !isImageImport(record))
  const selectedImages = selectedRecords.filter((record) => isImageImport(record) && record.visual_analysis_status !== 'succeeded')
  const cancellableVisualJobIds = selectedRecords
    .filter((record) => isImageImport(record) && ['queued', 'running'].includes(record.visual_analysis_status) && record.visual_analysis_job_id)
    .map((record) => record.visual_analysis_job_id)
  const selectableRecords = records.filter((record) => !isSupersededImportRecord(record))
  const allVisibleSelected = selectableRecords.length > 0 && selectableRecords.every((record) => selectedJobIds.includes(record.id))
  const selectedJob = selected?.job || null
  const visualJobs = Array.isArray(selected?.visual_jobs) ? selected.visual_jobs : []
  const latestVisualJob = visualJobs[0] || null

  const toggleSelected = useCallback((jobId) => { setSelectedJobIds((current) => current.includes(jobId) ? current.filter((id) => id !== jobId) : [...current, jobId]) }, [])
  function toggleAllVisible() { setSelectedJobIds(allVisibleSelected ? [] : selectableRecords.map((record) => record.id)) }
  function summarize(results, action) {
    const counts = (Array.isArray(results) ? results : []).reduce((summary, item) => { const key = item.outcome || 'failed'; summary[key] = (summary[key] || 0) + 1; return summary }, {})
    const labels = { queued: '已排队', queued_retry: '已重试排队', already_queued: '已在队列', submitted: '已提交', created: '已准备', deleted: '已删除', cancelled: '已取消', cancel_requested: '已请求取消', will_complete: '将完成', skipped: '已跳过', failed: '失败' }
    const text = Object.entries(counts).map(([key, count]) => `${labels[key] || key} ${count} 条`)
    return `${action}：${text.join('，') || '没有可处理的记录'}。`
  }

  const openDetail = useCallback(async (jobId) => {
    setDetailLoading(true); setSelected(null); setError(''); setApproveExternal(false)
    try { setSelected(await adminApi.getIngestionRecord(jobId)) }
    catch (detailError) { setError(detailError.message || '无法读取该导入记录') }
    finally { setDetailLoading(false) }
  }, [])

  async function reimportSelected() {
    if (!selectedJob) return
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const created = await adminApi.reimportIngestionJob(selectedJob.id)
      await load(); await openDetail(created.id); onImported()
      setNotice(`已从本地归档创建新的导入记录：${created.source_file_name || selectedJob.source_file_name}。未向外部服务发送文件。`)
    } catch (operationError) { setError(operationError.message || '无法重新导入该文件') }
    finally { setOperationBusy(false) }
  }

  async function queueOneImage() {
    if (!selectedJob) return
    if (!approveExternal) { setError('发送图片到外部模型前，请先勾选明确授权。'); return }
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.queueVisualJobs([selectedJob.id])
      rememberVisualJobIds(payload.jobs || [])
      await Promise.all([load(), openDetail(selectedJob.id)])
      onImported(); setNotice(summarize(payload.results, '图片分析已进入后台队列'))
    } catch (operationError) { setError(operationError.message || '图片分析排队失败') }
    finally { setOperationBusy(false) }
  }

  async function cancelOneImage() {
    if (!latestVisualJob) return
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.cancelVisualJobs([latestVisualJob.id])
      await Promise.all([load(), openDetail(selectedJob.id)])
      setNotice(summarize(payload.results, '图片分析取消操作已处理'))
    } catch (operationError) { setError(operationError.message || '取消图片分析任务失败') }
    finally { setOperationBusy(false) }
  }

  async function submitSelectedDocuments() {
    if (!selectedDocuments.length) { setError('请选择至少一个非图片文件。图片应使用“一键分析图片”。'); return }
    if (!approveBatchExternal) { setError('提交文档到已配置的外部解析服务前，请先勾选明确授权。'); return }
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.submitIngestionJobs(selectedDocuments.map((record) => record.id))
      setNotice(summarize(payload.results, '批量导入知识库已进入后台队列'))
      await load(); onImported()
    } catch (operationError) { setError(operationError.message || '批量导入知识库排队失败') }
    finally { setOperationBusy(false) }
  }

  function requestSelectedImageAnalysis() {
    if (!selectedImages.length) { setError('请选择至少一个尚未完成图片分析的图片记录。'); return }
    if (selectedImages.length > (queueInfo?.max_images || 20)) { setError(`一次图片分析最多选择 ${queueInfo?.max_images || 20} 张图片，请分批处理。`); return }
    if (!approveBatchExternal) { setError('发送图片到外部模型前，请先勾选明确授权。'); return }
    setError(''); setQueueConfirm(true)
  }

  async function confirmSelectedImageAnalysis() {
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.queueVisualJobs(selectedImages.map((record) => record.id))
      rememberVisualJobIds(payload.jobs || [])
      setQueueConfirm(false)
      setNotice(summarize(payload.results, '图片分析已进入后台队列；点击“刷新记录”可获取最新状态'))
      await load(); onImported()
    } catch (operationError) { setError(operationError.message || '图片分析排队失败') }
    finally { setOperationBusy(false) }
  }

  async function cancelSelectedImages() {
    if (!cancellableVisualJobIds.length) { setError('所选记录中没有可取消的图片分析队列任务。'); return }
    if (!window.confirm(`确认取消 ${cancellableVisualJobIds.length} 个图片分析任务吗？未开始的任务不会发送；正在执行的任务会在结果写入知识库前取消，或在已开始写入时允许完成。`)) return
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.cancelVisualJobs(cancellableVisualJobIds)
      setNotice(summarize(payload.results, '图片分析取消操作已处理'))
      await load(); onImported()
    } catch (operationError) { setError(operationError.message || '取消图片分析任务失败') }
    finally { setOperationBusy(false) }
  }

  async function deleteSelected() {
    if (!selectedJobIds.length) return
    if (!window.confirm(`确认移除选中的 ${selectedJobIds.length} 条导入记录吗？已写入知识库的资料和原始文件不会被删除；正在处理的记录会被安全跳过。`)) return
    setOperationBusy(true); setError(''); setNotice('')
    try {
      const payload = await adminApi.deleteIngestionJobs(selectedJobIds)
      setNotice(summarize(payload.results, '批量移除已处理')); setSelectedJobIds([]); setSelected(null); await load(); onImported()
    } catch (operationError) { setError(operationError.message || '批量删除失败') }
    finally { setOperationBusy(false) }
  }

  return <section className="content-page">
    <header className="page-header import-record-page-header"><div><p className="eyebrow">IMPORT LIFECYCLE</p><h1>导入任务记录</h1><p className="subtitle">逐个查看每一份文件的导入状态、处理方式和知识库写入情况。</p></div><button className="secondary-button" onClick={load} disabled={loading || operationBusy}>刷新记录</button></header>
    {error && <section className="error-banner"><strong>操作未完成：</strong>{error}</section>}
    {notice && <section className="success-banner">{notice}</section>}
    <section className="panel import-records-panel">
      <div className="record-toolbar"><label>导入状态<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} disabled={operationBusy}><option value="">全部状态</option><option value="created">已归档 · 待继续</option><option value="queued">排队中</option><option value="processing">导入处理中</option><option value="submitted">导入处理中</option><option value="succeeded">导入成功</option><option value="failed">导入失败</option><option value="cancelled">已取消</option></select></label><label>项目<select value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)} disabled={operationBusy}><option value="">全部项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label className="record-superseded-toggle"><input type="checkbox" checked={showSuperseded} onChange={(event) => setShowSuperseded(event.target.checked)} disabled={operationBusy} />显示已被成功结果替代的重复任务</label><span className="record-total">共 {total} 条记录</span></div>
      {selectedJobIds.length > 0 && <ModalPortal><section className="batch-toolbar bulk-selection-float" role="region" aria-label="已选择导入记录的批量操作"><div className="batch-summary"><strong>已选 {selectedJobIds.length} 条</strong><span>文档 {selectedDocuments.length} · 图片 {selectedImages.length}</span></div><label className="approval-check batch-approval"><input type="checkbox" checked={approveBatchExternal} onChange={(event) => setApproveBatchExternal(event.target.checked)} />我明确同意将选中的文档/图片发送至已配置的外部服务处理</label><div className="batch-actions"><button className="primary-button" onClick={submitSelectedDocuments} disabled={operationBusy || !selectedDocuments.length}>一键导入知识库</button><button className="secondary-button" onClick={requestSelectedImageAnalysis} disabled={operationBusy || !selectedImages.length}>一键分析图片</button>{cancellableVisualJobIds.length > 0 && <button className="secondary-button" onClick={cancelSelectedImages} disabled={operationBusy}>取消图片队列</button>}<button className="danger-button" onClick={deleteSelected} disabled={operationBusy}>一键删除</button></div></section></ModalPortal>}
      {loading ? <p className="empty">正在读取导入记录…</p> : !records.length ? <p className="empty">暂无导入记录。请先在“导入项目”中选择文件。</p> : <><div className="import-record-head"><span>选择</span><span>文件</span><span>处理状态</span><span>更新时间</span><span /></div><div className="import-record-list">{records.map((record) => <ImportRecordRow key={record.id} record={record} recordStore={recordStoreRef.current} selected={selectedJobIds.includes(record.id)} disabled={operationBusy} onToggle={toggleSelected} onOpen={openDetail} />)}</div></>}
    </section>
    {(selectedJob || detailLoading) && <ModalPortal><div className="modal-backdrop" onMouseDown={() => setSelected(null)}><aside className="detail-drawer import-record-detail" onMouseDown={(event) => event.stopPropagation()}>{detailLoading ? <p className="empty">正在读取导入详情…</p> : selectedJob ? <><button className="close-button" onClick={() => setSelected(null)}>×</button><p className="eyebrow">IMPORT RECORD</p><h2>{selectedJob.source_file_name || selectedJob.file_name}</h2><p className="muted">{selectedJob.project_name} · {selectedJob.project_external_key}</p><div className="detail-badges"><IngestionStatusBadge status={selectedJob.status} record={selectedJob} />{selectedJob.visual_analysis_status && <StatusBadge status={selectedJob.visual_analysis_status} />}</div><dl><dt>文件类型与大小</dt><dd>{selectedJob.file_type?.toUpperCase() || '—'} · {formatBytes(selectedJob.source_size_bytes)}</dd><dt>导入路径</dt><dd>{selectedJob.processing_route || '—'}</dd><dt>当前阶段</dt><dd>{selectedJob.route_reason || '—'}</dd><dt>创建时间</dt><dd>{formatDate(selectedJob.created_at)}</dd><dt>最近更新</dt><dd>{formatDate(selectedJob.updated_at)}</dd>{selectedJob.completed_at && <><dt>完成时间</dt><dd>{formatDate(selectedJob.completed_at)}</dd></>}{selectedJob.error_message && <><dt>错误信息</dt><dd className="detail-error">{selectedJob.error_message}</dd></>}</dl><div className="detail-actions"><button className="secondary-button" onClick={reimportSelected} disabled={operationBusy}>重新导入</button>{isImageImport(selectedJob) && (!latestVisualJob || ['created', 'failed', 'cancelled'].includes(latestVisualJob.status)) && <><label className="approval-check"><input type="checkbox" checked={approveExternal} onChange={(event) => setApproveExternal(event.target.checked)} />我已确认可将此图片发送到模型 {queueInfo?.model_id || '（正在读取模型）'} 进行分析</label><button className="primary-button" onClick={queueOneImage} disabled={operationBusy || !queueInfo?.configured}>加入图片分析队列</button></>}{latestVisualJob && ['queued', 'running'].includes(latestVisualJob.status) && <button className="secondary-button" onClick={cancelOneImage} disabled={operationBusy}>取消图片队列</button>}{latestVisualJob?.status === 'succeeded' && <button className="secondary-button" onClick={() => setActivePage('analysis')}>查看图片分析结果</button>}</div>{isImageImport(selectedJob) && <section className="detail-visual-history"><div className="panel-heading"><div><p className="eyebrow">VISION ANALYSIS</p><h3>图片分析记录</h3></div><span>{visualJobs.length} 条</span></div>{!visualJobs.length ? <p className="empty">尚未加入图片分析队列；授权后加入队列即可后台处理。</p> : <div className="result-list">{visualJobs.map((visualJob) => <article className="result-card" key={visualJob.id}><div><strong>{visualJob.model_id || '视觉模型'}</strong><small>{formatDate(visualJob.updated_at || visualJob.created_at)} · 重试 {visualJob.retry_count || 0}/1{visualJob.error_message ? ` · ${visualJob.error_message}` : ''}</small></div><StatusBadge status={visualJob.status} /></article>)}</div>}</section>}</> : <p className="empty">导入详情不可用，请关闭后重试。</p>}</aside></div></ModalPortal>}
    {queueConfirm && <ModalPortal><div className="modal-backdrop queue-confirm-backdrop" onMouseDown={() => !operationBusy && setQueueConfirm(false)}><section className="queue-confirm-dialog" onMouseDown={(event) => event.stopPropagation()}><button className="close-button" onClick={() => setQueueConfirm(false)} disabled={operationBusy}>×</button><p className="eyebrow">VISION QUEUE CONFIRMATION</p><h2>确认提交图片分析队列</h2><dl><dt>图片数量</dt><dd>{selectedImages.length} 张</dd><dt>将使用的模型</dt><dd>{queueInfo?.model_id || '正在读取地地模型配置…'}</dd><dt>处理规则</dt><dd>每张图片最多一个活跃任务；失败最多重试 1 次并记录原因。未开始的任务可取消且不会发送；运行中的任务会在写入知识库前取消，或在已开始写入时允许完成。</dd></dl><div className="dialog-actions"><button className="secondary-button" onClick={() => setQueueConfirm(false)} disabled={operationBusy}>返回</button><button className="primary-button" onClick={confirmSelectedImageAnalysis} disabled={operationBusy || !queueInfo?.configured}>{operationBusy ? '正在加入队列…' : '确认并加入队列'}</button></div></section></div></ModalPortal>}
  </section>
}
function AnalysisPage({ refreshKey }) {
  const [tasks, setTasks] = useState([])
  const [batches, setBatches] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await adminApi.listImportBatches({ limit: 50 })
      const rows = Array.isArray(payload.items) ? payload.items : []
      const details = await Promise.all(rows.map((batch) => adminApi.getImportBatch(batch.id).catch(() => null)))
      const flattened = details.filter(Boolean).flatMap((batch) => {
        const itemsById = new Map((batch.items || []).map((item) => [item.id, item]))
        return (batch.tasks || []).filter((task) => task.task_kind === 'vision').map((task) => {
          const source = itemsById.get(task.item_id) || {}
          return {
            ...task,
            batch_id: batch.id,
            batch_status: batch.status,
            project_name: batch.project_name,
            project_external_key: batch.project_external_key,
            source_file_name: batchTaskSourceName({ ...task, source_file_name: source.source_file_name }),
            source_relative_path: source.source_relative_path || task.details?.source_document_name || task.file_name,
          }
        })
      }).sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')))
      setBatches(rows)
      setTasks(flattened)
    } catch (loadError) {
      setError(loadError.message || '无法读取批次视觉分析任务')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  const openDetail = useCallback(async (task) => {
    setSelected(task)
    if (!task.visual_analysis_job_id) return
    setDetailLoading(true)
    try {
      const result = await adminApi.getVisualJob(task.visual_analysis_job_id)
      setSelected((current) => current?.id === task.id ? { ...task, ...result, source_file_name: task.source_file_name, source_relative_path: task.source_relative_path, batch_id: task.batch_id, batch_status: task.batch_status } : current)
    } catch (loadError) {
      setSelected((current) => current?.id === task.id ? { ...current, detail_error: loadError.message || '视觉分析详情暂不可用' } : current)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  return <section className="content-page">
    <header className="page-header"><div><p className="eyebrow">VISION ANALYSIS</p><h1>视觉分析结果</h1><p className="subtitle">显示所有批次中的图片及文档拆分页面，不依赖当前浏览器本机保存过的任务 ID。</p></div><button className="secondary-button" onClick={load} disabled={loading}>刷新文件</button></header>
    {error && <section className="error-banner">{error}</section>}
    <section className="panel analysis-inventory-panel"><div className="analysis-inventory-heading"><div><strong>{tasks.length}</strong><span>个视觉分析文件 / 页面</span></div><small>{batches.length} 个批次 · 点击“刷新文件”获取最新状态</small></div>
      {loading ? <p className="empty">正在读取所有批次的视觉分析文件…</p> : !tasks.length ? <p className="empty">当前批次中尚未生成视觉分析任务。请先在“导入项目”完成本地归档和明确授权。</p> : <div className="analysis-grid">{tasks.map((task) => <article className="analysis-card" key={task.id} onClick={() => openDetail(task)}><div className="analysis-card-top"><span className="file-type">{task.file_type?.toUpperCase() || 'IMG'}</span><StatusBadge status={batchTaskStatus(task)} /></div><h3 title={task.source_file_name}>{task.source_file_name}</h3><p>{task.project_name} · {batchTaskPageLabel(task)}</p><small className="analysis-path" title={task.source_relative_path}>{task.source_relative_path}</small><div className="analysis-meta"><span>{formatBytes(task.source_size_bytes)}</span><span>{task.external_target || 'GPT Vision'}</span></div>{batchTaskError(task) && <div className="analysis-error">{batchTaskError(task)}</div>}</article>)}</div>}
    </section>
    {selected && <ModalPortal><div className="modal-backdrop" onMouseDown={() => setSelected(null)}><aside className="detail-drawer wide" onMouseDown={(event) => event.stopPropagation()}><button className="close-button" onClick={() => setSelected(null)}>×</button><p className="eyebrow">IMAGE ANALYSIS DETAIL</p><h2>{selected.source_file_name || batchTaskSourceName(selected)}</h2><p className="muted">{selected.project_name} · {batchTaskPageLabel(selected)} · {selected.model_id || selected.external_target || '视觉模型'}</p><dl><dt>原始路径</dt><dd className="chunk-ids">{selected.source_relative_path || '—'}</dd><dt>页面 / 图片文件</dt><dd>{selected.file_name || '—'} · {formatBytes(selected.source_size_bytes)}</dd><dt>任务状态</dt><dd><StatusBadge status={batchTaskStatus(selected)} /></dd><dt>分析摘要</dt><dd>{detailLoading ? '正在读取视觉分析结果…' : selected.analysis?.summary || selected.detail_error || '尚未完成，完成后可在此查看结果。'}</dd><dt>空间类型</dt><dd>{(selected.analysis?.space_types || []).join('、') || '—'}</dd><dt>风格</dt><dd>{selected.analysis?.style?.primary || '—'} · 置信度 {selected.analysis?.style?.confidence ?? '—'}</dd><dt>材料</dt><dd>{(selected.analysis?.materials || []).map((item) => item.name).join('、') || '—'}</dd><dt>检索描述</dt><dd className="chunk-ids">{selected.analysis?.search_text || '—'}</dd><dt>限制说明</dt><dd>{(selected.analysis?.limitations || []).join('；') || '—'}</dd></dl></aside></div></ModalPortal>}
  </section>
}


function evaluationStatusLabel(status) { return ({ queued: '等待执行', running: '评测中', succeeded: '已完成', partially_succeeded: '部分完成', failed: '失败' })[status] || status || '—' }
function evaluationScore(value) { return typeof value === 'number' ? value.toFixed(3) : '—' }

function RagQualityPage({ projects }) {
  const [capabilities, setCapabilities] = useState(null); const [suites, setSuites] = useState([]); const [suiteId, setSuiteId] = useState(''); const [suite, setSuite] = useState(null); const [runs, setRuns] = useState([]); const [selectedRun, setSelectedRun] = useState(null); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [approval, setApproval] = useState(false); const [topK, setTopK] = useState(6); const [suiteDraft, setSuiteDraft] = useState({ project_id: '', name: '', description: '' }); const [caseDraft, setCaseDraft] = useState({ question: '', reference_answer: '', enabled: true, sort_order: 0 }); const [editingCaseId, setEditingCaseId] = useState('')

  const loadSuites = useCallback(async () => {
    const payload = await adminApi.listRagEvaluationSuites()
    const items = payload.items || []
    setSuites(items)
    setSuiteId((current) => current && items.some((item) => item.id === current) ? current : (items[0]?.id || ''))
  }, [])
  const loadSuite = useCallback(async (id) => {
    if (!id) { setSuite(null); setRuns([]); return }
    const [suitePayload, runsPayload] = await Promise.all([adminApi.getRagEvaluationSuite(id), adminApi.listRagEvaluationRuns({ suite_id: id })])
    setSuite(suitePayload); setRuns(runsPayload.items || [])
  }, [])
  const loadAll = useCallback(async () => {
    setLoading(true); setError('')
    try { const [capabilitiesPayload] = await Promise.all([adminApi.getRagEvaluationCapabilities(), loadSuites()]); setCapabilities(capabilitiesPayload) }
    catch (requestError) { setError(requestError.message || '无法读取 RAGAS 评测配置') }
    finally { setLoading(false) }
  }, [loadSuites])
  useEffect(() => { loadAll() }, [loadAll])
  useEffect(() => { loadSuite(suiteId).catch((requestError) => setError(requestError.message || '无法读取评测集')) }, [suiteId, loadSuite])
  useEffect(() => {
    if (!runs.some((run) => ['queued', 'running'].includes(run.status))) return undefined
    const timer = window.setInterval(() => { loadSuite(suiteId).catch(() => {}) ; if (selectedRun?.id) adminApi.getRagEvaluationRun(selectedRun.id).then(setSelectedRun).catch(() => {}) }, 3000)
    return () => window.clearInterval(timer)
  }, [runs, suiteId, loadSuite, selectedRun?.id])

  async function createSuite(event) { event.preventDefault(); setBusy(true); setError(''); setNotice(''); try { const created = await adminApi.createRagEvaluationSuite(suiteDraft); setSuiteDraft({ project_id: '', name: '', description: '' }); await loadSuites(); setSuiteId(created.id); setNotice('评测集已创建。请添加有标准答案的测试用例。') } catch (requestError) { setError(requestError.message || '创建评测集失败') } finally { setBusy(false) } }
  async function saveCase(event) { event.preventDefault(); if (!suiteId) return; setBusy(true); setError(''); setNotice(''); try { if (editingCaseId) await adminApi.updateRagEvaluationCase(editingCaseId, caseDraft); else await adminApi.createRagEvaluationCase(suiteId, { ...caseDraft, sort_order: Number(caseDraft.sort_order) || 0 }); setCaseDraft({ question: '', reference_answer: '', enabled: true, sort_order: (suite?.cases?.length || 0) + 1 }); setEditingCaseId(''); await loadSuite(suiteId); await loadSuites(); setNotice('测试用例已保存。') } catch (requestError) { setError(requestError.message || '保存测试用例失败') } finally { setBusy(false) } }
  function editCase(item) { setEditingCaseId(item.id); setCaseDraft({ question: item.question, reference_answer: item.reference_answer, enabled: item.enabled, sort_order: item.sort_order }) }
  async function removeCase(item) { if (!window.confirm('确认删除此测试用例吗？历史评测运行保留在数据库中。')) return; setBusy(true); setError(''); try { await adminApi.deleteRagEvaluationCase(item.id); if (editingCaseId === item.id) { setEditingCaseId(''); setCaseDraft({ question: '', reference_answer: '', enabled: true, sort_order: 0 }) }; await loadSuite(suiteId); await loadSuites(); setNotice('测试用例已删除。') } catch (requestError) { setError(requestError.message || '删除测试用例失败') } finally { setBusy(false) } }
  async function queueRun() { if (!suiteId || !approval) return; setBusy(true); setError(''); setNotice(''); try { const run = await adminApi.queueRagEvaluationRun({ suiteId, evidenceTopK: Number(topK), approved: approval }); setSelectedRun(run); await loadSuite(suiteId); setNotice('RAGAS 评测已进入本地队列，页面会每 3 秒刷新结果。') } catch (requestError) { setError(requestError.message || '启动评测失败') } finally { setBusy(false) } }
  async function showRun(runId) { setBusy(true); setError(''); try { setSelectedRun(await adminApi.getRagEvaluationRun(runId)) } catch (requestError) { setError(requestError.message || '无法读取评测结果') } finally { setBusy(false) } }

  const scoreSummary = selectedRun?.summary?.scores || {}
  return <section className="content-page quality-page">
    <header className="page-header"><div><p className="eyebrow">RAGAS QUALITY EVALUATION</p><h1>知识库质量评测</h1><p className="subtitle">用标准问题和标准答案，评测当前项目的检索证据与依据证据生成回答的一致性。</p></div><button className="secondary-button" onClick={loadAll} disabled={loading || busy}>刷新数据</button></header>
    {error && <section className="error-banner"><strong>操作失败：</strong>{error}</section>}{notice && <section className="notice-banner" role="status">{notice}</section>}
    <section className="quality-info"><div><strong>评测指标</strong><span>Faithfulness（回答是否由检索依据支持） · Context Precision（检索结果是否精准） · Context Recall（检索依据是否覆盖标准答案）</span></div><div><strong>外发边界</strong><span>只提交测试问题、标准答案、生成回答和已检索片段给已配置评测模型；不会提交原始文件、文件路径、凭据或未检索语料。</span></div><div><strong>运行状态</strong><span>{!capabilities ? '正在读取评测服务状态' : capabilities.available ? `RAGAS ${capabilities.version || ''} 可用` : 'RAGAS 当前不可用'} · 每次最多 {capabilities?.max_cases_per_run ?? '—'} 个启用用例</span></div></section>
    <div className="quality-grid">
      <section className="panel quality-suites"><div className="panel-heading"><div><p className="eyebrow">EVALUATION SUITES</p><h2>评测集</h2></div><span>{suites.length} 个</span></div><div className="quality-suite-list">{suites.map((item) => <button type="button" key={item.id} className={suiteId === item.id ? 'quality-suite active' : 'quality-suite'} onClick={() => { setSuiteId(item.id); setSelectedRun(null); setEditingCaseId('') }}><strong>{item.name}</strong><span>{item.project_name}</span><small>{item.enabled_case_count || 0}/{item.case_count || 0} 个用例启用</small></button>)}{!loading && !suites.length && <p className="empty">还没有评测集。请先为一个已导入项目创建评测集。</p>}</div><form className="quality-form compact" onSubmit={createSuite}><h3>新建评测集</h3><label>绑定项目<select required value={suiteDraft.project_id} onChange={(event) => setSuiteDraft((draft) => ({ ...draft, project_id: event.target.value }))}><option value="">选择已导入项目</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.external_key}</option>)}</select></label><label>名称<input required maxLength="200" placeholder="例如：绿城下沙 · 材料问答基准" value={suiteDraft.name} onChange={(event) => setSuiteDraft((draft) => ({ ...draft, name: event.target.value }))} /></label><label>说明<textarea maxLength="2000" placeholder="说明该评测集的覆盖范围" value={suiteDraft.description} onChange={(event) => setSuiteDraft((draft) => ({ ...draft, description: event.target.value }))} /></label><button className="primary-button" disabled={busy || !projects.length}>创建评测集</button></form></section>
      <div className="quality-main">{!suiteId ? <section className="panel"><p className="empty">请选择或创建一个评测集。</p></section> : <><section className="panel"><div className="panel-heading"><div><p className="eyebrow">TEST CASES</p><h2>{suite?.name || '正在读取…'}</h2><p className="muted">{suite?.project_name || ''}{suite?.description ? ` · ${suite.description}` : ''}</p></div><span>{suite?.cases?.length || 0} 个用例</span></div><div className="quality-case-list">{suite?.cases?.map((item) => <article className="quality-case-card" key={item.id}><div><span className={`quality-enabled ${item.enabled ? 'on' : ''}`}>{item.enabled ? '启用' : '停用'}</span><strong>#{item.sort_order} · {item.question}</strong><p>{item.reference_answer}</p></div><div className="quality-case-actions"><button className="secondary-button" type="button" onClick={() => editCase(item)} disabled={busy}>编辑</button><button className="danger-button" type="button" onClick={() => removeCase(item)} disabled={busy}>删除</button></div></article>)}{suite && !suite.cases?.length && <p className="empty">添加测试问题和可核验的标准答案后，才能开始评测。</p>}</div><form className="quality-form" onSubmit={saveCase}><h3>{editingCaseId ? '编辑测试用例' : '添加测试用例'}</h3><label>测试问题<textarea required maxLength="8000" placeholder="例如：电视背景墙采用什么材料？" value={caseDraft.question} onChange={(event) => setCaseDraft((draft) => ({ ...draft, question: event.target.value }))} /></label><label>标准答案<textarea required maxLength="16000" placeholder="只填写可以由项目资料核验的标准答案。" value={caseDraft.reference_answer} onChange={(event) => setCaseDraft((draft) => ({ ...draft, reference_answer: event.target.value }))} /></label><div className="quality-form-row"><label>排序<input type="number" min="0" max="100000" value={caseDraft.sort_order} onChange={(event) => setCaseDraft((draft) => ({ ...draft, sort_order: event.target.value }))} /></label><label className="quality-check"><input type="checkbox" checked={caseDraft.enabled} onChange={(event) => setCaseDraft((draft) => ({ ...draft, enabled: event.target.checked }))} />本次评测启用</label></div><div className="quality-form-actions"><button className="primary-button" disabled={busy}>{editingCaseId ? '保存修改' : '添加用例'}</button>{editingCaseId && <button className="secondary-button" type="button" onClick={() => { setEditingCaseId(''); setCaseDraft({ question: '', reference_answer: '', enabled: true, sort_order: 0 }) }}>取消编辑</button>}</div></form></section>
      <section className="panel quality-run-panel"><div className="panel-heading"><div><p className="eyebrow">RUN RAGAS</p><h2>开始评测</h2></div>{runs[0] && <span>最近：{formatDate(runs[0].created_at)}</span>}</div><div className="quality-run-controls"><label>检索依据数（Top K）<input type="number" min="1" max="20" value={topK} onChange={(event) => setTopK(event.target.value)} /></label><label className="quality-approval"><input type="checkbox" checked={approval} onChange={(event) => setApproval(event.target.checked)} /><span>我确认允许将测试问题、标准答案、生成回答和已检索片段发送至已配置的评测模型；不会发送原始文件、路径、凭据或未检索语料。</span></label><button className="primary-button" type="button" onClick={queueRun} disabled={busy || !approval || !capabilities?.available || !(suite?.cases || []).some((item) => item.enabled)}>开始 RAGAS 评测</button></div></section>
      <section className="panel quality-run-history"><div className="panel-heading"><div><p className="eyebrow">EVALUATION HISTORY</p><h2>运行记录</h2></div><span>{runs.length} 次</span></div>{!runs.length ? <p className="empty">暂无评测记录。</p> : <div className="quality-run-list">{runs.map((run) => <button type="button" className={selectedRun?.id === run.id ? 'quality-run active' : 'quality-run'} key={run.id} onClick={() => showRun(run.id)}><span className={`quality-run-status ${run.status}`}>{evaluationStatusLabel(run.status)}</span><strong>{formatDate(run.created_at)}</strong><small>{run.succeeded_case_count || 0}/{run.case_result_count || 0} 个用例完成 · Top {run.evidence_top_k}</small><div>{Object.entries(run.summary?.scores || {}).map(([key, value]) => <span className="quality-mini-score" key={key}>{key}: {evaluationScore(value)}</span>)}</div></button>)}</div>}</section>
      {selectedRun && <section className="panel quality-results"><div className="panel-heading"><div><p className="eyebrow">RUN RESULT</p><h2>{evaluationStatusLabel(selectedRun.status)}</h2><p className="muted">运行于 {formatDate(selectedRun.created_at)} · 模型 {selectedRun.model_id || '—'}</p></div><button type="button" className="secondary-button" onClick={() => showRun(selectedRun.id)} disabled={busy}>刷新结果</button></div>{selectedRun.error_message && <section className="error-banner">{selectedRun.error_message}</section>}<div className="quality-scores">{['faithfulness', 'context_precision', 'context_recall'].map((key) => <article key={key}><span>{key}</span><strong>{evaluationScore(scoreSummary[key])}</strong></article>)}</div><p className="muted">成功 {selectedRun.summary?.succeeded_case_count || 0} 个 · 失败 {selectedRun.summary?.failed_case_count || 0} 个</p><div className="quality-result-list">{(selectedRun.case_results || []).map((result) => <article className="quality-result-card" key={result.id || result.case_id}><div className="quality-result-top"><span className={`quality-run-status ${result.status}`}>{result.status === 'succeeded' ? '完成' : '失败'}</span><strong>{result.question}</strong></div>{result.error_message ? <p className="error-text">{result.error_message}</p> : <><dl><dt>标准答案</dt><dd>{result.reference_answer}</dd><dt>生成回答</dt><dd>{result.generated_answer || '—'}</dd><dt>得分</dt><dd>{Object.entries(result.scores || {}).map(([key, value]) => <span className="quality-mini-score" key={key}>{key}: {evaluationScore(value)}</span>)}</dd><dt>引用依据</dt><dd>{(result.citations || []).length ? <ul className="quality-citation-list">{result.citations.map((citation, index) => <li key={`${citation.source_id || index}-${citation.chunk_id || ''}`}>{citation.citation?.file_name || '未知文件'} · 第 {citation.citation?.page_start || '—'} 页 · {citation.citation?.section_path || '未标注章节'}</li>)}</ul> : '生成回答没有返回可引用的依据。'}</dd></dl><details><summary>查看已检索片段（{(result.retrieved_contexts || []).length}）</summary>{(result.retrieved_contexts || []).map((context, index) => <article className="quality-context" key={context.chunk_id || index}><strong>{context.citation?.file_name || '未知文件'} · 第 {context.citation?.page_start || '—'} 页</strong><p>{context.content}</p></article>)}</details></>}</article>)}</div></section>}</>}</div>
    </div>
  </section>
}


const MATERIAL_AUDIT_LABEL = {
  waiting_for_visual: '等待视觉分析', queued: '排队中', running: '核对中', succeeded: '已完成',
  partially_succeeded: '部分完成', failed: '失败', cancelled: '已取消',
}
const MATERIAL_VERDICT = {
  consistent: '一致', suspected_inconsistent: '疑似不一致', unable_to_confirm: '无法确认', missing_materials: '缺少资料',
}
function MaterialAuditStatus({ status }) { return <span className={`material-audit-status status-${status}`}>{MATERIAL_AUDIT_LABEL[status] || status}</span> }
function MaterialVerdict({ verdict }) { return <span className={`material-verdict ${verdict}`}>{MATERIAL_VERDICT[verdict] || verdict}</span> }
function sourceLabel(source) { return `${source.file_name || '未命名图片'}${source.page_number ? ` · 第 ${source.page_number} 页` : ''}` }
function sameSource(a, b) { return a.source_id === b.source_id && a.source_type === b.source_type }

function MaterialConsistencyPage({ projects }) {
  const [projectId, setProjectId] = useState('')
  const [sources, setSources] = useState(null)
  const [audits, setAudits] = useState([])
  const [audit, setAudit] = useState(null)
  const [selectedSources, setSelectedSources] = useState([])
  const [selectedDocuments, setSelectedDocuments] = useState([])
  const [analyzeMissing, setAnalyzeMissing] = useState(true)
  const [approved, setApproved] = useState(false)
  const [filter, setFilter] = useState('all')
  const [selectedItem, setSelectedItem] = useState(null)
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { if (!projectId && projects.length) setProjectId(projects[0].id) }, [projects, projectId])
  const load = useCallback(async ({ preserveAudit = false } = {}) => {
    if (!projectId) return
    setLoading(true); setError('')
    try {
      const [sourcePayload, auditPayload] = await Promise.all([
        adminApi.getMaterialConsistencySources(projectId), adminApi.listMaterialConsistencyAudits({ project_id: projectId }),
      ])
      setSources(sourcePayload)
      setAudits(auditPayload.items || [])
      setSelectedSources((current) => current.length ? current.filter((item) => (sourcePayload.images || []).some((row) => sameSource(item, row))) : (sourcePayload.images || []).filter((row) => row.can_analyze).map((row) => ({ source_id: row.source_id, source_type: row.source_type })))
      setSelectedDocuments((current) => current.length ? current.filter((id) => (sourcePayload.material_documents || []).some((row) => row.document_id === id)) : (sourcePayload.material_documents || []).map((row) => row.document_id))
      if (!preserveAudit && auditPayload.items?.length) setAudit(auditPayload.items[0])
    } catch (e) { setError(e.message || '无法读取核对资料') } finally { setLoading(false) }
  }, [projectId])
  useEffect(() => { setAudit(null); setSelectedItem(null); load() }, [load])

  useEffect(() => {
    if (!audit?.id || !['waiting_for_visual', 'queued', 'running'].includes(audit.status)) return undefined
    const stream = new EventSource(adminApi.materialConsistencyEventsUrl(audit.id))
    const refresh = async () => {
      try {
        const full = await adminApi.getMaterialConsistencyAudit(audit.id)
        setAudit(full)
        setAudits((rows) => rows.map((row) => row.id === full.id ? { ...row, ...full } : row))
      } catch { /* the endpoint can be retried manually without changing page layout */ }
    }
    stream.addEventListener('progress', refresh)
    stream.addEventListener('completed', refresh)
    stream.onerror = () => { stream.close() }
    return () => stream.close()
  }, [audit?.id, audit?.status])

  const toggleSource = (source) => setSelectedSources((current) => current.some((item) => sameSource(item, source)) ? current.filter((item) => !sameSource(item, source)) : [...current, { source_id: source.source_id, source_type: source.source_type }])
  const toggleDocument = (id) => setSelectedDocuments((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  const missingVisualCount = useMemo(() => (sources?.images || []).filter((source) => selectedSources.some((item) => sameSource(item, source)) && source.visual_analysis_status !== 'succeeded').length, [sources, selectedSources])
  const items = audit?.items || []
  const visibleItems = filter === 'all' ? items : items.filter((item) => item.verdict === filter)
  const groups = useMemo(() => visibleItems.reduce((result, item) => { const key = `${item.source_type}:${item.source_id}`; (result[key] ||= { label: `${item.source_file_name}${item.page_number ? ` · 第 ${item.page_number} 页` : ''}`, items: [] }).items.push(item); return result }, {}), [visibleItems])
  const summary = audit?.summary || {}

  async function startAudit() {
    setCreating(true); setError('')
    try {
      const created = await adminApi.createMaterialConsistencyAudit({
        project_id: projectId, sources: selectedSources, material_document_ids: selectedDocuments,
        analyze_missing_images: analyzeMissing, explicit_external_export_approved: approved,
      })
      setAudit(created); setAudits((rows) => [created, ...rows.filter((row) => row.id !== created.id)])
    } catch (e) { setError(e.message || '无法创建核对任务') } finally { setCreating(false) }
  }
  async function refreshResult() {
    if (!audit?.id) return load({ preserveAudit: true })
    try { setAudit(await adminApi.getMaterialConsistencyAudit(audit.id)) } catch (e) { setError(e.message || '无法刷新核对结果') }
  }
  async function cancelAudit() {
    if (!audit?.id) return
    try { setAudit(await adminApi.cancelMaterialConsistencyAudit(audit.id)) } catch (e) { setError(e.message || '无法取消核对任务') }
  }

  return <section className="content-page material-consistency-page">
    <header className="page-header material-page-header"><div><p className="eyebrow">MATERIAL CONSISTENCY</p><h1>效果图物料核对</h1><p className="subtitle">将效果图的结构化视觉分析与同项目物料资料逐项比对。结果采用保守结论，所有材料依据都保留文件、页码、章节和 chunk ID。</p></div><div className="material-header-actions"><button className="secondary-button" onClick={() => load({ preserveAudit: true })} disabled={loading}>刷新资料</button>{audit && <button className="secondary-button" onClick={refreshResult}>刷新结果</button>}</div></header>
    {error && <p className="analysis-error">{error}</p>}
    <section className="panel material-config-panel">
      <div className="material-project-bar"><label>项目<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">选择项目</option>{projects.map((project) => <option value={project.id} key={project.id}>{project.name} · {project.external_key}</option>)}</select></label><div><strong>{selectedSources.length}</strong><span> 已选效果图</span><strong>{selectedDocuments.length}</strong><span> 已选物料资料</span></div></div>
      {!projectId || loading ? <p className="empty">正在读取当前项目的效果图和物料资料…</p> : <div className="material-selection-grid">
        <div className="material-source-column"><div className="panel-heading"><div><p className="eyebrow">EFFECT RENDERS</p><h2>效果图与设计页面</h2></div><span>{sources?.images?.length || 0} 条</span></div><div className="material-selection-list">{(sources?.images || []).map((source) => { const checked = selectedSources.some((item) => sameSource(item, source)); const analyzed = source.visual_analysis_status === 'succeeded'; return <label className={`material-source-row${checked ? ' selected' : ''}`} key={`${source.source_type}-${source.source_id}`}><input type="checkbox" checked={checked} disabled={!source.can_analyze} onChange={() => toggleSource(source)} /><span className="material-source-icon">{source.source_type === 'image_asset' ? '▧' : '▦'}</span><span className="material-source-copy"><strong>{sourceLabel(source)}</strong><small>{analyzed ? '视觉分析已完成' : source.source_type === 'image_asset' ? '提取设计页，将在授权后分析' : `视觉分析：${source.visual_analysis_status || '未提交'}`}</small></span><span>{analyzed ? <MaterialAuditStatus status="succeeded" /> : <MaterialAuditStatus status={source.visual_analysis_status || 'waiting_for_visual'} />}</span></label> }) || <p className="empty">未发现可用于核对的效果图或设计页面。</p>}</div></div>
        <div className="material-source-column"><div className="panel-heading"><div><p className="eyebrow">MATERIAL EVIDENCE</p><h2>物料书依据</h2></div><span>{sources?.material_documents?.length || 0} 份</span></div><div className="material-selection-list">{(sources?.material_documents || []).map((doc) => <label className={`material-source-row${selectedDocuments.includes(doc.document_id) ? ' selected' : ''}`} key={doc.document_id}><input type="checkbox" checked={selectedDocuments.includes(doc.document_id)} onChange={() => toggleDocument(doc.document_id)} /><span className="material-source-icon">▤</span><span className="material-source-copy"><strong>{doc.file_name}</strong><small>{doc.file_type?.toUpperCase() || '文件'} · {doc.evidence_count} 条原始材料依据{doc.first_page ? ` · 从第 ${doc.first_page} 页开始` : ''}</small></span></label>) || <p className="empty">当前项目尚未发现材料表、选型表或可追溯材料文本。</p>}</div></div>
      </div>}
      <div className="material-action-bar"><div>{missingVisualCount > 0 && <p className="material-warning">已选 {missingVisualCount} 张效果图或提取设计页尚未完成视觉分析。勾选下方选项后会先补齐分析，再继续核对。</p>}{!selectedDocuments.length && <p className="material-warning">未选择物料资料：任务仍会生成“缺少资料”报告，不会用其他项目的证据补全。</p>}<label className="approval-check"><input type="checkbox" checked={analyzeMissing} onChange={(event) => setAnalyzeMissing(event.target.checked)} />补齐缺少的视觉分析</label><label className="approval-check"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />我同意将必要的 EXIF 清除图片（仅在缺少视觉分析时）、结构化视觉结果和选定物料片段发送给配置的 AI；不发送原始归档文件或其他项目资料</label></div><button className="primary-button" onClick={startAudit} disabled={creating || !projectId || !selectedSources.length || !approved}>{creating ? '正在创建…' : '开始核对'}</button></div>
    </section>
    <section className="material-workspace">
      <aside className="panel material-history"><div className="panel-heading"><div><p className="eyebrow">AUDIT HISTORY</p><h2>核对记录</h2></div></div>{!audits.length ? <p className="empty">尚无核对任务。</p> : <div className="material-history-list">{audits.map((row) => <button type="button" className={`material-history-row${audit?.id === row.id ? ' active' : ''}`} key={row.id} onClick={async () => { try { setAudit(await adminApi.getMaterialConsistencyAudit(row.id)); setSelectedItem(null) } catch (e) { setError(e.message || '无法打开核对任务') } }}><span><strong>{formatDate(row.created_at)}</strong><small>{row.summary?.total_components || row.item_count || 0} 个构件</small></span><MaterialAuditStatus status={row.status} /></button>)}</div>}</aside>
      <div className="material-results">
        {!audit ? <section className="panel"><p className="empty">选择项目后开始核对，或从左侧选择历史记录。</p></section> : <>
          <section className="material-report-toolbar"><div><p className="eyebrow">CURRENT REPORT</p><h2>{audit.project_name || sources?.project?.name || '项目核对报告'}</h2><p>{MATERIAL_AUDIT_LABEL[audit.status]} · {audit.progress?.message || '等待任务状态'}</p></div><div className="report-toolbar-actions"><MaterialAuditStatus status={audit.status} />{['waiting_for_visual', 'queued', 'running'].includes(audit.status) && <button className="secondary-button" onClick={cancelAudit}>取消任务</button>}</div></section>
          <section className="metrics material-metrics"><MetricCard label="总构件" value={summary.total_components || 0} detail="已生成的核对项" /><MetricCard label="一致" value={summary.consistent || 0} detail="可追溯的直接对应" tone="positive" /><MetricCard label="疑似不一致" value={summary.suspected_inconsistent || 0} detail="存在明确材质冲突" tone="warning" /><MetricCard label="待人工确认" value={(summary.unable_to_confirm || 0) + (summary.missing_materials || 0)} detail={`无法确认 ${summary.unable_to_confirm || 0} · 缺少资料 ${summary.missing_materials || 0}`} tone="cool" /></section>
          <section className="panel material-report-panel"><div className="material-filter-bar"><div>{[['all', '全部'], ['consistent', '仅看一致'], ['suspected_inconsistent', '仅看疑似不一致'], ['unable_to_confirm', '仅看无法确认'], ['missing_materials', '仅看缺少资料']].map(([value, label]) => <button type="button" className={filter === value ? 'filter-chip active' : 'filter-chip'} onClick={() => setFilter(value)} key={value}>{label}</button>)}</div><small>引用已按文档、页码和 chunk ID 去重</small></div>{!items.length ? <p className="empty">{['waiting_for_visual', 'queued', 'running'].includes(audit.status) ? '任务正在进行，进度会通过实时连接更新。' : '此任务尚未生成可展示的核对项。'}</p> : <div className="material-result-groups">{Object.entries(groups).map(([key, group]) => <section className="material-result-group" key={key}><h3>{group.label}<span>{group.items.length} 项</span></h3>{group.items.map((item) => <article className="material-result-row" key={item.id}><div className="material-result-main"><MaterialVerdict verdict={item.verdict} /><div><strong>{item.component_name}</strong><p>{item.reason}</p><small>{item.visual_material?.generic_material || item.visual_material?.material_category || '未识别可见材质'} {item.visual_material?.color ? `· ${item.visual_material.color}` : ''}</small></div></div><div className="material-result-side"><strong>{Math.round((item.confidence || 0) * 100)}%</strong><button className="detail-button" onClick={() => setSelectedItem(item)}>查看依据</button></div></article>)}</section>)}</div>}</section>
        </>}
      </div>
    </section>
    {selectedItem && <ModalPortal><div className="modal-backdrop" onMouseDown={() => setSelectedItem(null)}><aside className="detail-drawer material-detail-drawer" onMouseDown={(event) => event.stopPropagation()}><button className="close-button" onClick={() => setSelectedItem(null)}>×</button><p className="eyebrow">TRACEABLE EVIDENCE</p><h2>{selectedItem.component_name}</h2><div className="detail-badges"><MaterialVerdict verdict={selectedItem.verdict} /><span>{Math.round((selectedItem.confidence || 0) * 100)}% 置信度</span></div><div className="material-preview"><img src={adminApi.materialConsistencyPreviewUrl({ projectId: audit?.project_id, sourceType: selectedItem.source_type, sourceId: selectedItem.source_id })} alt={`${selectedItem.source_file_name} 预览`} /><div className="material-bbox" style={bboxStyle(selectedItem.component_region)} /></div><dl className="material-detail-list"><dt>效果图</dt><dd>{selectedItem.source_file_name}{selectedItem.page_number ? ` · 第 ${selectedItem.page_number} 页` : ''}</dd><dt>可见构件与材质</dt><dd>{selectedItem.visual_material?.element || selectedItem.component_name} · {selectedItem.visual_material?.generic_material || selectedItem.visual_material?.material_category || '未明确'}</dd><dt>物料书要求</dt><dd>{selectedItem.material_requirement?.summary || '没有可确认的物料书要求'}</dd><dt>判断原因</dt><dd>{selectedItem.reason}</dd></dl><section className="material-evidence-list"><h3>物料书依据</h3>{(selectedItem.evidence || []).length ? selectedItem.evidence.map((evidence) => <article key={`${evidence.document_id}-${evidence.page_start}-${evidence.chunk_id}`}><strong>{evidence.file_name || '未知文件'} · 第 {evidence.page_start || '—'} 页</strong><small>{evidence.section_path || '未标注章节'} · chunk {evidence.chunk_id || '—'}</small><p>{evidence.excerpt}</p><em>匹配词：{(evidence.matched_terms || []).join('、') || '—'}</em></article>) : <p className="empty">没有可追溯的物料书依据。</p>}</section>{selectedItem.ai_judgement?.reason && <section className="material-ai-note"><h3>AI 保守判断</h3><p>{selectedItem.ai_judgement.reason}</p>{selectedItem.ai_judgement.limitations?.length ? <small>限制：{selectedItem.ai_judgement.limitations.join('；')}</small> : null}</section>}</aside></div></ModalPortal>}
  </section>
}

function bboxStyle(bbox) {
  if (!bbox || typeof bbox !== 'object') return { display: 'none' }
  const left = Number(bbox.x ?? bbox.left); const top = Number(bbox.y ?? bbox.top); const width = Number(bbox.width ?? (bbox.right != null && bbox.left != null ? bbox.right - bbox.left : NaN)); const height = Number(bbox.height ?? (bbox.bottom != null && bbox.top != null ? bbox.bottom - bbox.top : NaN))
  if (![left, top, width, height].every(Number.isFinite)) return { display: 'none' }
  const scale = Math.max(left, top, width, height) > 1.5 ? 100 : 1
  return { left: `${left / scale * 100}%`, top: `${top / scale * 100}%`, width: `${width / scale * 100}%`, height: `${height / scale * 100}%` }
}

function getInitialThemeMode() {
  const saved = localStorage.getItem('design-rag-theme')
  return saved === 'dark' || saved === 'system' ? saved : 'light'
}

function getSystemTheme() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function ThemeSwitcher({ themeMode, onChange }) {
  const options = [
    { value: 'light', label: '浅色', icon: '☼' },
    { value: 'system', label: '系统', icon: '▣' },
    { value: 'dark', label: '深色', icon: '☾' },
  ]
  return <div className="theme-switcher" role="group" aria-label="主题模式">
    <span className="theme-switcher-track" aria-hidden="true"><span className={`theme-switcher-thumb theme-${themeMode}`} /></span>
    <div className="theme-switcher-options">
      {options.map((option) => <button key={option.value} type="button" className={`theme-switcher-option${themeMode === option.value ? ' active' : ''}`} onClick={() => onChange(option.value)} aria-pressed={themeMode === option.value} title={`${option.label}主题`}>
        <span aria-hidden="true">{option.icon}</span><span>{option.label}</span>
      </button>)}
    </div>
  </div>
}

function HarnessPage({ projects }) {
  const [projectId, setProjectId] = useState(''); const [runType, setRunType] = useState('assets'); const [runs, setRuns] = useState([]); const [selected, setSelected] = useState(null); const [prompt, setPrompt] = useState(''); const [generation, setGeneration] = useState(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const load = useCallback(async () => { try { const result = await adminApi.listHarnessRuns(projectId ? { project_id: projectId } : {}); setRuns(result.items || []) } catch (e) { setError(e.message) } }, [projectId])
  useEffect(() => { load() }, [load])
  async function startRun() { setBusy(true); setError(''); try { const row = await adminApi.createHarnessRun({ project_id: projectId || null, run_type: runType }); setRuns((items) => [row, ...items]); setSelected(row) } catch (e) { setError(e.message) } finally { setBusy(false) } }
  async function createGeneration() { if (!projectId || !prompt.trim()) return; setBusy(true); try { setGeneration(await adminApi.createImageGenerationJob({ project_id: projectId, prompt, reference_asset_ids: [], citation_ids: [] })) } catch (e) { setError(e.message) } finally { setBusy(false) } }
  async function confirmGeneration() { if (!generation) return; setBusy(true); try { setGeneration(await adminApi.confirmImageGenerationJob(generation.id)) } catch (e) { setError(e.message) } finally { setBusy(false) } }
  return <section className="content-page harness-page"><header className="page-header"><div><p className="eyebrow">DESIGN QUALITY WORKBENCH</p><h1>Design Harness</h1><p className="subtitle">验证项目资产、RAG/MCP 依据，并创建可追溯的 AI 方案图片。</p></div><button className="secondary-button" onClick={load}>刷新记录</button></header>{error && <section className="error-banner">{error}</section>}<section className="panel harness-config"><label>项目<select value={projectId} onChange={(e)=>setProjectId(e.target.value)}><option value="">选择项目</option>{projects.map((p)=><option key={p.id} value={p.id}>{p.name} · {p.external_key}</option>)}</select></label><label>测试类型<select value={runType} onChange={(e)=>setRunType(e.target.value)}><option value="assets">资产核验</option><option value="rag">RAG 检索</option><option value="mcp">MCP 契约</option><option value="ragas">RAGAS</option><option value="all">全部</option></select></label><button className="primary-button" disabled={busy || !projectId} onClick={startRun}>创建评测任务</button></section><div className="harness-grid"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">RUN HISTORY</p><h2>评测记录</h2></div><span>{runs.length} 次</span></div>{runs.length ? runs.map((row)=><button type="button" className={`harness-run${selected?.id===row.id?' active':''}`} key={row.id} onClick={()=>setSelected(row)}><strong>{row.project_name || '全部项目'}</strong><span>{row.run_type} · {row.status}</span><small>{formatDate(row.created_at)}</small></button>) : <p className="empty">暂无评测任务。</p>}</section><section className="panel harness-generation"><div className="panel-heading"><div><p className="eyebrow">IMAGE GENERATION</p><h2>生成项目方案图</h2></div><span>人工确认后提交</span></div><textarea value={prompt} onChange={(e)=>setPrompt(e.target.value)} placeholder="描述空间、风格、材料和需要生成的画面"/><div className="harness-actions"><button className="primary-button" disabled={busy || !projectId || !prompt.trim()} onClick={createGeneration}>创建生成任务</button>{generation && generation.status==='awaiting_confirmation' && <button className="secondary-button" disabled={busy} onClick={confirmGeneration}>确认并提交 ChatGPT</button>}</div>{generation && <div className="generation-job"><strong>{generation.project_name} · {generation.status}</strong><p>{generation.prompt}</p><small>输出类型：AI 方案参考 · 参考图片：{(generation.reference_asset_ids||[]).length} 张</small></div>}</section></div>{selected && <section className="panel harness-detail"><div className="panel-heading"><h2>任务详情</h2><button className="secondary-button" onClick={async()=>setSelected(await adminApi.getHarnessRun(selected.id))}>刷新详情</button></div><pre>{JSON.stringify(selected,null,2)}</pre></section>}</section>
}
function App() {
  const [activePage, setActivePage] = useState('overview')
  const [themeMode, setThemeMode] = useState(getInitialThemeMode)
  const [systemTheme, setSystemTheme] = useState(getSystemTheme)
  const [projects, setProjects] = useState([])
  const [refreshKey, setRefreshKey] = useState(0)
  const [importWorkspace, setImportWorkspace] = useImportWorkspace()

  const resolvedTheme = themeMode === 'system' ? systemTheme : themeMode

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme
    document.documentElement.dataset.themeMode = themeMode
    localStorage.setItem('design-rag-theme', themeMode)
  }, [resolvedTheme, themeMode])

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!media) return undefined
    const update = (event) => setSystemTheme(event.matches ? 'dark' : 'light')
    media.addEventListener?.('change', update)
    return () => media.removeEventListener?.('change', update)
  }, [])

  useEffect(() => { adminApi.getProjects().then((payload) => setProjects(payload.items || [])).catch(() => {}) }, [])

  function imported() {
    setRefreshKey((value) => value + 1)
    adminApi.getProjects().then((payload) => setProjects(payload.items || [])).catch(() => {})
  }

  return <div className="admin-layout">
    <aside className="sidebar">
      <div className="sidebar-scroll">
        <div className="brand-lockup">
          <div className="brand-mark">DR</div>
          <div className="brand-copy"><strong>Design_RAG</strong><span>LOCAL WORKSPACE</span></div>
        </div>
        <nav className="admin-nav" aria-label="管理后台导航">
          {NAV_SECTIONS.map((section) => <section className="nav-section" key={section.label}>
            <h2>{section.label}</h2>
            <div className="nav-section-items">
              {section.items.map((item) => { const Icon = item.icon; return <button type="button" key={item.id} className={`nav-item${activePage === item.id ? ' active' : ''}`} onClick={() => setActivePage(item.id)} aria-current={activePage === item.id ? 'page' : undefined}>
                <Icon aria-hidden="true" /><span>{item.label}</span>
              </button> })}
            </div>
          </section>)}
        </nav>
      </div>
      <div className="sidebar-bottom">
        <a className="ai-switch-link" href={chatWorkspaceUrl()} aria-label="进入智能问答"><span className="ai-switch-icon" aria-hidden="true">✦</span><span className="ai-switch-copy"><strong>进入智能问答</strong><small>CHAT WORKSPACE</small></span><span className="ai-switch-arrow" aria-hidden="true">↗</span></a>
        <ThemeSwitcher themeMode={themeMode} onChange={setThemeMode} />
        <small className="sidebar-status"><span className="status-dot" aria-hidden="true" />本地后台 · 8030</small>
      </div>
    </aside>
    <main className="main-area">
      {activePage === 'overview' && <OverviewPage projects={projects} setProjects={setProjects} />}
      {activePage === 'import' && <ImportPage onImported={imported} workspace={importWorkspace} setWorkspace={setImportWorkspace} />}
      {activePage === 'analysis' && <AnalysisPage refreshKey={refreshKey} />}
      {activePage === 'material-consistency' && <MaterialConsistencyPage projects={projects} />}
      {activePage === 'batch-records' && <BatchImportRecordsPage refreshKey={refreshKey} />}
      {activePage === 'import-records' && <ImportRecordsPage projects={projects} refreshKey={refreshKey} onImported={imported} setActivePage={setActivePage} />}
      {activePage === 'quality' && <RagQualityPage projects={projects} />}
      {activePage === 'harness' && <HarnessPage projects={projects} />}
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
