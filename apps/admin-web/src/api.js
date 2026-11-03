const configuredBase = (import.meta.env.VITE_DESIGN_RAG_ADMIN_API_BASE_URL || '').replace(/\/$/, '')
const localAdminBase = 'http://127.0.0.1:8030'

function apiUrl(path, params) {
  const isLocalFile = window.location.origin === 'null'
  const base = configuredBase || (isLocalFile ? localAdminBase : '')
  const url = new URL(`${base}${path}`, isLocalFile ? localAdminBase : window.location.origin)
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value))
  }
  return url.toString()
}

async function parseResponse(response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`)
  return payload
}

async function request(path, options = {}, params) {
  const url = apiUrl(path, params)
  let response
  try {
    response = await fetch(url, {
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    })
  } catch (error) {
    const target = new URL(url).origin
    throw new Error(`无法连接管理 API（${target}）。请使用当前页面所在地址的 /admin/ 打开管理端，并确认 8030 服务正在运行。`)
  }
  return parseResponse(response)
}

export const adminApi = {
  getProjects: () => request('/api/v1/admin/projects'),
  listIngestionJobs: (filters = {}) => request('/api/v1/admin/ingestion-jobs', {}, filters),
  getIngestionJobStatuses: (jobIds) => request('/api/v1/admin/ingestion-jobs/status-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: jobIds }),
  }),
  getIngestionRecord: (jobId) => request(`/api/v1/admin/ingestion-jobs/${jobId}`),
  reimportIngestionJob: (jobId) => request(`/api/v1/ingestion-jobs/${jobId}/reimport`, { method: 'POST' }),
  submitIngestionJobs: (ingestionJobIds) => request('/api/v1/ingestion-jobs/submit-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ explicit_external_export_approved: true, ingestion_job_ids: ingestionJobIds }),
  }),
  deleteIngestionJobs: (jobIds) => request('/api/v1/admin/ingestion-jobs', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_delete: true, job_ids: jobIds }),
  }),
  getSummary: (days) => request('/api/v1/admin/search-audit/summary', {}, { days }),
  getSearchAudit: (filters) => request('/api/v1/admin/search-audit', {}, filters),
  createImportBatch: ({ projectExternalKey, projectName, forceReprocess = false }) => request('/api/v1/admin/import-batches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_external_key: projectExternalKey, project_name: projectName, force_reprocess: forceReprocess }),
  }),
  uploadImportBatchFile: ({ batchId, file }) => {
    const sourceRelativePath = file.designRagRelativePath || file.webkitRelativePath || file.name
    const form = new FormData()
    form.append('file', file)
    form.append('source_relative_path', sourceRelativePath)
    return request(`/api/v1/admin/import-batches/${batchId}/files`, { method: 'POST', body: form })
  },
  prepareImportBatch: (batchId) => request(`/api/v1/admin/import-batches/${batchId}/prepare`, { method: 'POST' }),
  getImportBatch: (batchId) => request(`/api/v1/admin/import-batches/${batchId}`),
  listImportBatches: (filters = {}) => request('/api/v1/admin/import-batches', {}, filters),
  approveImportBatch: ({ batchId, manifestSha256, approveMineru, approveVision, scheduleMode = 'immediate', scheduledFor = null }) => request(`/api/v1/admin/import-batches/${batchId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      manifest_sha256: manifestSha256,
      approve_mineru: approveMineru,
      approve_vision: approveVision,
      schedule_mode: scheduleMode,
      scheduled_for: scheduledFor,
    }),
  }),
  listImportBatchPageReviews: (batchId) => request(`/api/v1/admin/import-batches/${batchId}/page-reviews`),
  updateImportBatchPageReview: ({ batchId, pageReviewId, selectedProcessor }) => request(`/api/v1/admin/import-batches/${batchId}/page-reviews/${pageReviewId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected_processor: selectedProcessor }),
  }),
  bulkUpdateImportBatchPageReviews: ({ batchId, pageReviewIds, selectedProcessor }) => request(`/api/v1/admin/import-batches/${batchId}/page-reviews/bulk-update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page_review_ids: pageReviewIds, selected_processor: selectedProcessor }),
  }),
  confirmImportBatchPageReviews: (batchId) => request(`/api/v1/admin/import-batches/${batchId}/page-reviews/confirm`, { method: 'POST' }),
  reopenImportBatchPageReviews: (batchId) => request(`/api/v1/admin/import-batches/${batchId}/page-reviews/reopen`, { method: 'POST' }),
  importBatchPageReviewPreviewUrl: ({ batchId, pageReviewId, sha256 }) => apiUrl(`/api/v1/admin/import-batches/${batchId}/page-reviews/${pageReviewId}/preview`, { v: sha256 }),
  importBatchEventsUrl: (batchId) => apiUrl(`/api/v1/admin/import-batches/${batchId}/events`),
  uploadProjectFile: ({ file, projectExternalKey, projectName }) => {
    const sourceRelativePath = file.designRagRelativePath || file.webkitRelativePath || file.name
    const form = new FormData()
    form.append('file', file)
    form.append('project_external_key', projectExternalKey)
    form.append('project_name', projectName)
    form.append('document_external_key', `${projectExternalKey}-${sourceRelativePath.replace(/[\\/]+/g, '-')}`)
    form.append('document_metadata_json', JSON.stringify({ source: 'admin-import', source_relative_path: sourceRelativePath }))
    return request('/api/v1/ingestion-jobs', { method: 'POST', body: form })
  },
  getVisualQueueInfo: () => request('/api/v1/visual-analysis-jobs/queue-info'),
  queueVisualJobs: (ingestionJobIds) => request('/api/v1/visual-analysis-jobs/queue-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ explicit_external_export_approved: true, ingestion_job_ids: ingestionJobIds }),
  }),
  cancelVisualJobs: (jobIds) => request('/api/v1/visual-analysis-jobs/cancel-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: jobIds }),
  }),
  getVisualJob: (jobId) => request(`/api/v1/visual-analysis-jobs/${jobId}`),
  getRagEvaluationCapabilities: () => request('/api/v1/admin/rag-evaluations/capabilities'),
  listRagEvaluationSuites: (filters = {}) => request('/api/v1/admin/rag-evaluations/suites', {}, filters),
  createRagEvaluationSuite: (payload) => request('/api/v1/admin/rag-evaluations/suites', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  getRagEvaluationSuite: (suiteId) => request(`/api/v1/admin/rag-evaluations/suites/${suiteId}`),
  updateRagEvaluationSuite: (suiteId, payload) => request(`/api/v1/admin/rag-evaluations/suites/${suiteId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  createRagEvaluationCase: (suiteId, payload) => request(`/api/v1/admin/rag-evaluations/suites/${suiteId}/cases`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  updateRagEvaluationCase: (caseId, payload) => request(`/api/v1/admin/rag-evaluations/cases/${caseId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  deleteRagEvaluationCase: (caseId) => request(`/api/v1/admin/rag-evaluations/cases/${caseId}`, { method: 'DELETE' }),
  listRagEvaluationRuns: (filters = {}) => request('/api/v1/admin/rag-evaluations/runs', {}, filters),
  getRagEvaluationRun: (runId) => request(`/api/v1/admin/rag-evaluations/runs/${runId}`),
  queueRagEvaluationRun: ({ suiteId, evidenceTopK, approved }) => request(`/api/v1/admin/rag-evaluations/suites/${suiteId}/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ explicit_external_evaluation_approved: approved, evidence_top_k: evidenceTopK }),
  }),
  listHarnessProjects: () => request('/api/v1/admin/harness/projects'),
  listHarnessRuns: (params = {}) => request('/api/v1/admin/harness/runs', {}, params),
  createHarnessRun: (payload) => request('/api/v1/admin/harness/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getHarnessRun: (id) => request(`/api/v1/admin/harness/runs/${id}`),
  createImageGenerationJob: (payload) => request('/api/v1/admin/image-generation/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getImageGenerationJob: (id) => request(`/api/v1/admin/image-generation/jobs/${id}`),
  confirmImageGenerationJob: (id) => request(`/api/v1/admin/image-generation/jobs/${id}/confirm`, { method: 'POST' }),
  cancelImageGenerationJob: (id) => request(`/api/v1/admin/image-generation/jobs/${id}/cancel`, { method: 'POST' }),  getMaterialConsistencySources: (projectId) => request('/api/v1/admin/material-consistency/sources', {}, { project_id: projectId }),
  listMaterialConsistencyAudits: (filters = {}) => request('/api/v1/admin/material-consistency/audits', {}, filters),
  getMaterialConsistencyAudit: (auditId) => request(`/api/v1/admin/material-consistency/audits/${auditId}`),
  createMaterialConsistencyAudit: (payload) => request('/api/v1/admin/material-consistency/audits', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  cancelMaterialConsistencyAudit: (auditId) => request(`/api/v1/admin/material-consistency/audits/${auditId}/cancel`, { method: 'POST' }),
  materialConsistencyEventsUrl: (auditId) => apiUrl(`/api/v1/admin/material-consistency/audits/${auditId}/events`),
  materialConsistencyPreviewUrl: ({ projectId, sourceType, sourceId }) => apiUrl(`/api/v1/admin/material-consistency/sources/${sourceType}/${sourceId}/preview`, { project_id: projectId }),
}
