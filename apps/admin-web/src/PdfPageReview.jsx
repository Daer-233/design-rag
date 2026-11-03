import { useEffect, useMemo, useRef, useState } from 'react'
import { adminApi } from './api'

const PROCESSOR_LABEL = { mineru: 'MinerU', gpt_vision: 'GPT Vision', mineru_then_gpt: 'MinerU 提取 + GPT 识图' }
const TASK_LABEL = { pending: '待确认', awaiting_approval: '等待授权', queued: '排队中', processing: '处理中', succeeded: '已完成', failed: '失败' }
const MIN_ZOOM = 0.05
const MAX_ZOOM = 4

function clamp(value, minimum, maximum) { return Math.min(maximum, Math.max(minimum, value)) }

function reviewGroups(reviews) {
  const groups = new Map()
  reviews.forEach((review) => {
    const key = review.item_id || 'unknown'
    if (!groups.has(key)) groups.set(key, { id: key, name: review.source_file_name || 'PDF 文件', pages: [] })
    groups.get(key).pages.push(review)
  })
  return [...groups.values()].map((group) => ({ ...group, pages: group.pages.sort((a, b) => a.source_page_number - b.source_page_number) }))
}

function PageStatus({ review }) {
  const status = review.task_status || review.review_status || 'pending'
  return <span className={`pdf-review-status status-${status}`}>{TASK_LABEL[status] || status}</span>
}

export default function PdfPageReview({ batch, onBatchUpdated, disabled = false, onError, reviewState, onReviewStateChange }) {
  const [reviews, setReviews] = useState(() => Array.isArray(batch?.page_reviews) ? batch.page_reviews : [])
  const [activeItemId, setActiveItemId] = useState(() => reviewState?.activeItemId || '')
  const [activePageId, setActivePageId] = useState(() => reviewState?.activePageId || '')
  const [selectedIds, setSelectedIds] = useState(() => new Set(reviewState?.selectedIds || []))
  const [zoom, setZoom] = useState(() => Number(reviewState?.zoom) || 1)
  const [zoomMode, setZoomMode] = useState(() => reviewState?.zoomMode === 'manual' ? 'manual' : 'fit')
  const [rotation, setRotation] = useState(() => Number(reviewState?.rotation) || 0)
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 })
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 })
  const [busy, setBusy] = useState(false)
  const canvasRef = useRef(null)

  useEffect(() => {
    const next = Array.isArray(batch?.page_reviews) ? batch.page_reviews : []
    setReviews(next)
  }, [batch?.id, batch?.page_reviews])

  useEffect(() => {
    let disposed = false
    if (!batch?.id) return undefined
    adminApi.listImportBatchPageReviews(batch.id)
      .then((payload) => { if (!disposed && Array.isArray(payload.items)) setReviews(payload.items) })
      .catch((error) => { if (!disposed) onError?.(error.message || '无法读取 PDF 分页审核记录') })
    return () => { disposed = true }
  }, [batch?.id, onError])

  const groups = useMemo(() => reviewGroups(reviews), [reviews])
  const activeGroup = groups.find((group) => group.id === activeItemId) || groups[0]
  const pages = activeGroup?.pages || []
  const activePage = pages.find((page) => page.id === activePageId) || pages[0]
  const editable = batch?.status === 'awaiting_page_review' && !disabled && !busy
  const reopenable = batch?.status === 'awaiting_approval' && !disabled && !busy

  useEffect(() => {
    if (activeGroup && activeGroup.id !== activeItemId) setActiveItemId(activeGroup.id)
    if (activePage && activePage.id !== activePageId) setActivePageId(activePage.id)
  }, [activeGroup, activeItemId, activePage, activePageId])

  useEffect(() => {
    const node = canvasRef.current
    if (!node) return undefined
    const publishSize = () => setCanvasSize((current) => {
      const next = { width: node.clientWidth, height: node.clientHeight }
      return current.width === next.width && current.height === next.height ? current : next
    })
    publishSize()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publishSize)
    observer?.observe(node)
    window.addEventListener('resize', publishSize)
    return () => { observer?.disconnect(); window.removeEventListener('resize', publishSize) }
  }, [])

  useEffect(() => {
    setImageSize({ width: 0, height: 0 })
  }, [activePage?.id])

  useEffect(() => {
    onReviewStateChange?.({ activeItemId, activePageId, selectedIds: [...selectedIds], zoom, zoomMode, rotation })
  }, [activeItemId, activePageId, onReviewStateChange, rotation, selectedIds, zoom, zoomMode])

  const quarterTurn = Math.abs(rotation % 180) === 90
  const fitZoom = useMemo(() => {
    if (!imageSize.width || !imageSize.height || !canvasSize.width || !canvasSize.height) return 1
    const availableWidth = Math.max(1, canvasSize.width - 64)
    const availableHeight = Math.max(1, canvasSize.height - 64)
    const displayedWidth = quarterTurn ? imageSize.height : imageSize.width
    const displayedHeight = quarterTurn ? imageSize.width : imageSize.height
    return clamp(Math.min(availableWidth / displayedWidth, availableHeight / displayedHeight), MIN_ZOOM, MAX_ZOOM)
  }, [canvasSize.height, canvasSize.width, imageSize.height, imageSize.width, quarterTurn])
  const effectiveZoom = zoomMode === 'fit' ? fitZoom : zoom
  const renderedWidth = (quarterTurn ? imageSize.height : imageSize.width) * effectiveZoom
  const renderedHeight = (quarterTurn ? imageSize.width : imageSize.height) * effectiveZoom
  const imageWidth = imageSize.width * effectiveZoom
  const imageHeight = imageSize.height * effectiveZoom

  function publish(next) {
    if (next?.page_reviews) setReviews(next.page_reviews)
    onBatchUpdated?.(next)
  }
  function selectPage(page, multi = false) {
    setActivePageId(page.id)
    if (!multi) return
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(page.id)) next.delete(page.id)
      else next.add(page.id)
      return next
    })
  }
  function toggleSelectAllPages() {
    if (!editable || !pages.length) return
    setSelectedIds((current) => {
      const next = new Set(current)
      const shouldClear = pages.every((page) => next.has(page.id))
      pages.forEach((page) => {
        if (shouldClear) next.delete(page.id)
        else next.add(page.id)
      })
      return next
    })
  }
  function resetToFit() { setZoomMode('fit') }
  function adjustZoom(amount) {
    setZoom(clamp(effectiveZoom + amount, MIN_ZOOM, MAX_ZOOM))
    setZoomMode('manual')
  }
  async function updatePages(ids, processor) {
    // Action buttons may receive selected page objects from selected; the API
    // expects UUID strings only. Normalize here so [object Object] can never
    // leak into the PATCH URL or the bulk-update payload.
    const pageReviewIds = (Array.isArray(ids) ? ids : [ids])
      .map((page) => typeof page === 'object' && page !== null ? page.id : page)
      .filter(Boolean)
    if (!editable || !pageReviewIds.length) return
    setBusy(true)
    try {
      const next = pageReviewIds.length === 1
        ? await adminApi.updateImportBatchPageReview({ batchId: batch.id, pageReviewId: pageReviewIds[0], selectedProcessor: processor })
        : await adminApi.bulkUpdateImportBatchPageReviews({ batchId: batch.id, pageReviewIds, selectedProcessor: processor })
      publish(next)
    } catch (error) {
      onError?.(error.message || '无法更新页面处理方式')
    } finally { setBusy(false) }
  }
  async function confirm() {
    setBusy(true)
    try { publish(await adminApi.confirmImportBatchPageReviews(batch.id)); setSelectedIds(new Set()) }
    catch (error) { onError?.(error.message || '无法确认分页方案') }
    finally { setBusy(false) }
  }
  async function reopen() {
    setBusy(true)
    try { publish(await adminApi.reopenImportBatchPageReviews(batch.id)); setSelectedIds(new Set()) }
    catch (error) { onError?.(error.message || '无法重新打开分页审核') }
    finally { setBusy(false) }
  }

  if (!groups.length) return null
  const pageIndex = Math.max(0, pages.findIndex((page) => page.id === activePage?.id))
  const previewUrl = activePage ? adminApi.importBatchPageReviewPreviewUrl({ batchId: batch.id, pageReviewId: activePage.id, sha256: activePage.preview_sha256 }) : ''
  const selected = pages.filter((page) => selectedIds.has(page.id))
  const allPagesSelected = pages.length > 0 && pages.every((page) => selectedIds.has(page.id))

  return <section className="pdf-reviewer" aria-label="PDF 分页人工审核器">
    <header className="pdf-reviewer-heading">
      <div><p className="eyebrow">LOCAL PDF PAGE REVIEW</p><h3>逐页确认处理方式</h3><p className="muted">页面 JPG 仅在本机生成与预览。可选择先由 MinerU 提取文字、表格和图片，再只把 MinerU 提取出的图片交给 GPT 视觉模型；不会把整份 PDF 直接发送给 GPT。</p></div>
      <div className="pdf-reviewer-file-tabs" aria-label="选择 PDF 文件">{groups.map((group) => <button type="button" key={group.id} className={group.id === activeGroup?.id ? 'active' : ''} onClick={() => { setActiveItemId(group.id); setActivePageId(group.pages[0]?.id || ''); setSelectedIds(new Set()) }}>{group.name}<small>{group.pages.length} 页</small></button>)}</div>
    </header>
    <div className="pdf-review-filmstrip-toolbar">
      <div><strong>当前文件分页</strong><span>{pages.length} 页 · 已选择 {selected.length} 页</span></div>
      <button type="button" className="pdf-review-select-all" onClick={toggleSelectAllPages} disabled={!editable || !pages.length} aria-pressed={allPagesSelected} title="选择或取消选择当前 PDF 的全部页面">{allPagesSelected ? '取消全选' : '全选页'}</button>
    </div>
    <div className="pdf-review-filmstrip" role="list" aria-label="PDF 页面缩略图">
      {pages.map((page) => {
        const checked = selectedIds.has(page.id)
        const url = adminApi.importBatchPageReviewPreviewUrl({ batchId: batch.id, pageReviewId: page.id, sha256: page.preview_sha256 })
        return <article key={page.id} role="listitem" className={`pdf-review-thumb ${page.id === activePage?.id ? 'active' : ''} processor-${page.selected_processor}`}>
          <button type="button" className="pdf-review-thumb-image" onClick={() => selectPage(page)} title={`查看第 ${page.source_page_number} 页`}><img src={url} alt={`第 ${page.source_page_number} 页缩略图`} loading="lazy" /></button>
          <label className="pdf-review-select"><input type="checkbox" checked={checked} disabled={!editable} onChange={() => selectPage(page, true)} /><span>第 {page.source_page_number} 页</span></label>
          <div className="pdf-review-thumb-meta"><span title={PROCESSOR_LABEL[page.selected_processor]} className={`processor-badge processor-${page.selected_processor}`}>{PROCESSOR_LABEL[page.selected_processor]}</span><PageStatus review={page} /></div>
        </article>
      })}
    </div>
    <div className="pdf-review-stage">
      <div className="pdf-review-stage-toolbar">
        <div><button type="button" onClick={() => pages[pageIndex - 1] && selectPage(pages[pageIndex - 1])} disabled={!pages[pageIndex - 1]}>上一页</button><span>第 {activePage?.source_page_number || '—'} / {pages.length} 页</span><button type="button" onClick={() => pages[pageIndex + 1] && selectPage(pages[pageIndex + 1])} disabled={!pages[pageIndex + 1]}>下一页</button><label className="pdf-review-page-jump">跳转<select value={activePage?.id || ''} onChange={(event) => { const page = pages.find((item) => item.id === event.target.value); if (page) selectPage(page) }}><option value="" disabled>选择页码</option>{pages.map((page) => <option key={page.id} value={page.id}>第 {page.source_page_number} 页</option>)}</select></label></div>
        <div><button type="button" onClick={resetToFit}>适应窗口</button><button type="button" onClick={() => adjustZoom(-.15)}>－</button><span>{Math.round(effectiveZoom * 100)}%</span><button type="button" onClick={() => adjustZoom(.15)}>＋</button><button type="button" onClick={() => setRotation((value) => (value + 90) % 360)}>旋转</button></div>
      </div>
      <div className={`pdf-review-canvas ${zoomMode === 'fit' ? 'is-fit' : 'is-manual'}`} ref={canvasRef}>{activePage ? <div className="pdf-review-image-frame" style={imageSize.width ? { width: `${renderedWidth}px`, height: `${renderedHeight}px` } : undefined}><img src={previewUrl} alt={`${activeGroup?.name || 'PDF'}第 ${activePage.source_page_number} 页`} onLoad={(event) => { const next = { width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }; setImageSize((current) => current.width === next.width && current.height === next.height ? current : next) }} style={imageSize.width ? { width: `${imageWidth}px`, height: `${imageHeight}px`, transform: `translate(-50%, -50%) rotate(${rotation}deg)` } : undefined} /></div> : <p className="muted">暂无可预览的页面</p>}</div>
    </div>
    <footer className="pdf-review-actions">
      <div><strong>{selected.length ? `已选择 ${selected.length} 页` : `当前第 ${activePage?.source_page_number || '—'} 页`}</strong><small>{zoomMode === 'fit' ? '当前为整页适应模式；整张页面会随预览区尺寸自动调整。' : '当前为手动缩放模式；可在中间预览区滚动查看局部细节。'}</small></div>
      {editable && <div className="pdf-review-action-buttons"><button type="button" onClick={() => updatePages(selected.length ? selected : [activePage?.id].filter(Boolean), 'mineru')}>设为 MinerU</button><button type="button" onClick={() => updatePages(selected.length ? selected : [activePage?.id].filter(Boolean), 'gpt_vision')}>设为 GPT Vision</button><button type="button" onClick={() => updatePages(selected.length ? selected : [activePage?.id].filter(Boolean), 'mineru_then_gpt')}>MinerU 提取 + GPT 识图</button><button type="button" onClick={() => updatePages(reviews.map((page) => page.id), 'mineru')}>全部 MinerU</button><button type="button" onClick={() => updatePages(reviews.map((page) => page.id), 'gpt_vision')}>全部 GPT</button><button type="button" onClick={() => updatePages(reviews.map((page) => page.id), 'mineru_then_gpt')}>全部 MinerU + GPT</button><button type="button" className="primary-button" onClick={confirm}>{busy ? '正在确认…' : '确认分页方案'}</button></div>}
      {reopenable && <div className="pdf-review-action-buttons"><span className="local-only-hint">方案已冻结，尚未进入外部队列。</span><button type="button" onClick={reopen}>{busy ? '正在打开…' : '重新打开审核'}</button></div>}
      {batch?.status !== 'awaiting_page_review' && batch?.status !== 'awaiting_approval' && <span className="local-only-hint">此批次已进入执行流程，分页方案已锁定。</span>}
    </footer>
  </section>
}

