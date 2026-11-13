const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')

export type HealthResponse = {
  status: string
  service: string
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`, { signal })
  if (!response.ok) throw new Error(`API returned ${response.status}`)
  return response.json() as Promise<HealthResponse>
}

export type VideoRecord = {
  id: string
  original_filename: string
  file_size: number
  content_type: string
  upload_status: string
  created_at: string
}

export type VideoListResponse = {
  items: VideoRecord[]
  total: number
  limit: number
  offset: number
}

export type UploadProgress = {
  loaded: number
  total: number
  percent: number
  speedBytesPerSecond: number | null
  remainingSeconds: number | null
  phase: 'preparing' | 'uploading' | 'server' | 'completed' | 'failed' | 'cancelled'
}

export const UPLOAD_TIMEOUT_MS = 60 * 60 * 1000
export const UPLOAD_STALL_TIMEOUT_MS = 2 * 60 * 1000
export const UPLOAD_RESPONSE_TIMEOUT_MS = 5 * 60 * 1000

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const detail = body.detail
      if (typeof detail === 'string') return detail
      if (typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string') return detail.message
    }
  } catch {
    // Use the generic status message if the API response is not JSON.
  }
  return `Request failed (${response.status})`
}

export async function getVideos(signal?: AbortSignal, limit = 100, offset = 0): Promise<VideoListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  const response = await fetch(`${API_BASE_URL}/api/videos?${params}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<VideoListResponse>
}

export async function deleteVideo(videoId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/videos/${encodeURIComponent(videoId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(await readErrorMessage(response))
}

export type VideoUploadTask = {
  promise: Promise<VideoRecord>
  abort: () => void
}

export async function runVideoUpload(
  createTask: () => VideoUploadTask,
  onSuccess: (video: VideoRecord) => void,
  onFailure: (error: unknown) => void,
  onSettled: (task: VideoUploadTask | null) => void,
): Promise<void> {
  let task: VideoUploadTask | null = null
  try {
    task = createTask()
    const video = await task.promise
    onSuccess(video)
  } catch (error) {
    onFailure(error)
  } finally {
    onSettled(task)
  }
}

export type AnalysisStatus = 'queued' | 'running' | 'completed' | 'failed'

export type AnalysisJob = {
  job_id: string
  video_id: string
  status: AnalysisStatus
  progress_percentage: number
  current_stage: string
  frames_processed: number
  frames_sampled: number
  total_frames: number | null
  detections_count: number
  error_message: string | null
  confidence_threshold: number
  sampling_rate_fps: number
  image_size: number
  tracking_enabled: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type AnalysisJobListResponse = {
  items: AnalysisJob[]
  total: number
  limit: number
  offset: number
}

export type DetectionRecord = {
  detection_id: string
  job_id: string
  frame_index: number
  timestamp_seconds: number
  class_id: number
  class_name: string
  confidence: number
  bounding_box: [number, number, number, number]
  track_id: number | null
}

export type AnalysisResults = {
  job_id: string
  video_id: string
  total: number
  limit: number
  offset: number
  detections: DetectionRecord[]
}

export type TrackSummary = {
  track_id: number
  class_id: number
  class_name: string
  first_observed_seconds: number
  last_observed_seconds: number
  observation_count: number
  observed_span_seconds: number | null
}

export type TrackSummaryList = {
  job_id: string
  total_unique_tracked_objects: number
  total: number
  limit: number
  offset: number
  class_counts: Record<string, number>
  tracks: TrackSummary[]
}

export type AIStatus = {
  provider: 'lm_studio'
  endpoint: string
  model: string
  connected: boolean
  model_available: boolean
  inference_ready: boolean
  status: 'ready' | 'unavailable' | 'model_missing' | 'inference_unready'
  message: string
}

export type EvidenceReference = {
  type: 'detection_or_tracking'
  track_id: number | null
  timestamp: number
  detection_id: string
  class_id?: number | null
  class_name?: string | null
}

export type AIObservation = {
  title: string
  description: string
  evidence: EvidenceReference[]
  confidence: 'high' | 'medium' | 'low'
  limitations: string
}

export type AIRecommendation = {
  title: string
  description: string
  reason: string
  evidence: EvidenceReference[]
  requires_human_review: boolean
}

export type AIReport = {
  analysis_job_id: string
  generated_by: string
  generated_at: string
  summary: string
  observations: AIObservation[]
  recommendations: AIRecommendation[]
  limitations: string[]
  evidence: {
    analysis_job_id: string
    video_filename: string | null
    detection_count: number
    class_counts: { class_id: number; class_name: string; count: number; mean_confidence: number }[]
    unique_track_count: number
    tracks: { track_id: number; class_name: string; first_observed_seconds: number; last_observed_seconds: number; observation_count: number }[]
    tracked_detection_count?: number
    track_class_counts?: { class_id: number; class_name: string; unique_tracks: number }[]
    evidence_selection?: { track_summaries_included: number; representative_detections_included: number; selection_method: string; scope_note: string }
    statistics_scope?: string
    representative_detections: { detection_id: string; timestamp_seconds: number; class_id: number; class_name: string; track_id: number | null; confidence: number }[]
    [key: string]: unknown
  }
}

export type AIAnswer = {
  analysis_job_id: string
  answer: string
  evidence: EvidenceReference[]
  limitations: string[]
  generated_by: string
}

export type AgentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'idle'
export type AgentInfo = {
  agent_id: string; name: string; description: string; version: string; capabilities: string[]
  enabled: boolean; current_status: AgentStatus; current_task: string | null
  last_execution_at: string | null; last_duration_ms: number | null; result_summary: string | null
  error_details: { code?: string; message?: string; exception_class?: string } | null
}
export type AgentExecution = {
  agent_id: string; status: AgentStatus; current_task: string | null; started_at: string | null
  completed_at: string | null; duration_ms: number | null; result_summary: string | null
  error_details: { code?: string; message?: string; exception_class?: string } | null
}
export type AgentWorkflow = {
  workflow_id: string; job_id: string; status: 'pending' | 'validating' | 'running' | 'completed' | 'failed' | 'cancelled'
  current_agent: string | null; created_at: string; started_at: string | null; completed_at: string | null
  agent_execution_history: AgentExecution[]; result: Record<string, unknown> | null
  error_details: { code?: string; message?: string; exception_class?: string } | null
}

export async function getAgents(signal?: AbortSignal): Promise<AgentInfo[]> {
  const response = await fetch(`${API_BASE_URL}/api/agents`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentInfo[]>
}

export async function setAgentEnabled(agentId: string, enabled: boolean): Promise<AgentInfo> {
  const response = await fetch(`${API_BASE_URL}/api/agents/${encodeURIComponent(agentId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentInfo>
}

export async function startAgentWorkflow(jobId: string): Promise<AgentWorkflow> {
  const response = await fetch(`${API_BASE_URL}/api/agent-workflows`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId }),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentWorkflow>
}

export async function getAgentWorkflow(workflowId: string, signal?: AbortSignal): Promise<AgentWorkflow> {
  const response = await fetch(`${API_BASE_URL}/api/agent-workflows/${encodeURIComponent(workflowId)}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentWorkflow>
}

export async function getAgentWorkflows(signal?: AbortSignal): Promise<AgentWorkflow[]> {
  const response = await fetch(`${API_BASE_URL}/api/agent-workflows?limit=20`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentWorkflow[]>
}

export async function cancelAgentWorkflow(workflowId: string): Promise<AgentWorkflow> {
  const response = await fetch(`${API_BASE_URL}/api/agent-workflows/${encodeURIComponent(workflowId)}/cancel`, { method: 'POST' })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AgentWorkflow>
}

export type OperationalFilter = {
  analysis_job_id?: string
  video_id?: string
  date_from?: string
  date_to?: string
}

export type ActivityCategory = 'VIDEO' | 'ANALYSIS' | 'DETECTION' | 'TRACKING' | 'REASONING' | 'AGENT' | 'WORKFLOW' | 'OPERATIONS' | 'REVIEW' | 'SYSTEM' | 'SECURITY'
export type ActivityStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'SKIPPED'
export type ActivitySeverity = 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL'
export type ActivityQuery = {
  category?: ActivityCategory
  event_type?: string
  severity?: ActivitySeverity
  status?: ActivityStatus
  source?: string
  date_from?: string
  date_to?: string
  video_id?: string
  analysis_job_id?: string
  workflow_id?: string
  agent_id?: string
  search?: string
}
export type ActivityLog = {
  id: string
  category: ActivityCategory
  event_type: string
  title: string
  description: string
  severity: ActivitySeverity
  status: ActivityStatus
  source: string
  video_id: string | null
  analysis_job_id: string | null
  workflow_id: string | null
  agent_id: string | null
  operational_event_id: string | null
  timestamp: string
  duration_ms: number | null
  metadata: Record<string, unknown>
  error_type: string | null
}
export type ActivitySummary = {
  total_events: number
  successful_executions: number
  failed_executions: number
  warning_events: number
  recent_events: number
  recent_window_hours: 24
}
export type ActivityFilterOptions = {
  categories: ActivityCategory[]
  event_types: string[]
  severities: ActivitySeverity[]
  statuses: ActivityStatus[]
  sources: string[]
}
export type ActivityLogPage = { items: ActivityLog[]; total: number; limit: number; offset: number }

function activityParams(filters: ActivityQuery, extra?: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  Object.entries({ ...filters, ...extra }).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return params.toString()
}

export async function getActivityLogs(filters: ActivityQuery, signal?: AbortSignal, limit = 30, offset = 0): Promise<ActivityLogPage> {
  const query = activityParams(filters, { limit, offset })
  const response = await fetch(`${API_BASE_URL}/api/activity-logs?${query}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<ActivityLogPage>
}

export async function getActivityLog(id: string, signal?: AbortSignal): Promise<ActivityLog> {
  const response = await fetch(`${API_BASE_URL}/api/activity-logs/${encodeURIComponent(id)}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<ActivityLog>
}

export async function getActivitySummary(filters: ActivityQuery, signal?: AbortSignal): Promise<ActivitySummary> {
  const query = activityParams(filters)
  const response = await fetch(`${API_BASE_URL}/api/activity-logs/summary${query ? `?${query}` : ''}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<ActivitySummary>
}

export async function getActivityFilters(signal?: AbortSignal): Promise<ActivityFilterOptions> {
  const response = await fetch(`${API_BASE_URL}/api/activity-logs/filters`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<ActivityFilterOptions>
}

export async function exportActivityCsv(filters: ActivityQuery): Promise<Blob> {
  const query = activityParams(filters)
  const response = await fetch(`${API_BASE_URL}/api/activity-logs/export${query ? `?${query}` : ''}`)
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.blob()
}
export type OperationalOverview = {
  filters: OperationalFilter
  metrics: {
    total_videos_analysed: number; completed_analysis_jobs: number; total_detected_objects: number
    total_unique_tracked_entities: number; tracked_object_categories: string[]
    available_ai_reports: number; events_requiring_attention: number
  }
  recent_analyses: { analysis_job_id: string; video_id: string; video_filename: string; status: string
    detections_count: number; unique_tracked_entities: number; created_at: string }[]
  category_distribution: CategoryMetric[]
  detection_trend: { bucket: string; detection_count: number; tracked_detection_count: number }[]
}
export type CategoryMetric = { class_id: number; class_name: string; detection_count: number
  percent_of_detections: number; unique_tracked_entities: number }
export type OperationalAnalytics = {
  filters: OperationalFilter
  category_distribution: CategoryMetric[]
  confidence_distribution: { confidence_range: string; detection_count: number }[]
  detection_trend: { bucket: string; detection_count: number; tracked_detection_count: number }[]
  tracking_activity: { analysis_job_id: string; video_id: string; video_filename: string; track_id: number
    class_id: number; class_name: string; observation_count: number; first_observed_seconds: number
    last_observed_seconds: number }[]
  tracking_total: number; tracking_limit: number; tracking_offset: number
  job_comparisons: { analysis_job_id: string; video_id: string; video_filename: string; created_at: string
    detection_count: number; unique_tracked_entities: number }[]
}
export type OperationEvent = {
  event_id: string; event_type: string; severity: 'informational' | 'low' | 'medium' | 'high'
  title: string; description: string; analysis_job_id: string; video_id: string; event_timestamp: string
  evidence_explanation: string; rule_basis: Record<string, unknown>
  evidence: { detection_ids: string[]; timestamp_seconds: number | null; class_name: string | null }
  review_status: 'pending' | 'reviewed' | 'dismissed'; reviewed_at: string | null
  reviewer_identifier: string | null; review_note: string | null
}
export type OperationEventList = { items: OperationEvent[]; total: number; limit: number; offset: number }
export type OperationalRisk = { event_id: string; risk_category: string; severity: OperationEvent['severity']; title: string
  evidence_summary: string; analysis_job_id: string; video_id: string; related_detection_ids: string[]
  rule_triggered: Record<string, unknown>; suggested_next_action: string; requires_human_review: true
  review_status: OperationEvent['review_status'] }
export type OperationalRecommendation = { recommendation_id: string; priority: 'high' | 'medium' | 'low'
  title: string; description: string; reason: string; analysis_job_id: string | null; video_id: string | null
  evidence_references: string[]; source: 'rule_based' | 'ai_report'; requires_human_review: true }
export type OperationalAvailability = { available_data_sources: string[]; ai_report_available: boolean
  ai_runtime_required_for_basic_analytics: false
  capabilities: { capability: string; status: 'available' | 'partially_available' | 'not_implemented' | 'requires_custom_model'; details: string }[] }

function operationalQuery(filters: OperationalFilter, extras: Record<string, string | number | undefined> = {}) {
  const params = new URLSearchParams()
  Object.entries({ ...filters, ...extras }).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return params.toString()
}

async function operationalGet<T>(path: string, filters: OperationalFilter, signal?: AbortSignal, extras?: Record<string, string | number | undefined>): Promise<T> {
  const query = operationalQuery(filters, extras)
  const response = await fetch(`${API_BASE_URL}/api/operations/${path}${query ? `?${query}` : ''}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<T>
}

export const getOperationalOverview = (filters: OperationalFilter, signal?: AbortSignal) =>
  operationalGet<OperationalOverview>('overview', filters, signal)
export const getOperationalAnalytics = (filters: OperationalFilter, signal?: AbortSignal, limit = 8, offset = 0) =>
  operationalGet<OperationalAnalytics>('analytics', filters, signal, { limit, offset })
export const getOperationalEvents = (filters: OperationalFilter, signal?: AbortSignal, limit = 8, offset = 0,
  reviewStatus?: string, eventType?: string, severity?: string) =>
  operationalGet<OperationEventList>('events', filters, signal, { limit, offset, review_status: reviewStatus, event_type: eventType, severity })
export const getOperationalRisks = (filters: OperationalFilter, signal?: AbortSignal, limit = 8, offset = 0) =>
  operationalGet<{ items: OperationalRisk[]; total: number; limit: number; offset: number }>('risks', filters, signal, { limit, offset })
export const getOperationalRecommendations = (filters: OperationalFilter, signal?: AbortSignal, limit = 8, offset = 0) =>
  operationalGet<{ items: OperationalRecommendation[]; total: number; limit: number; offset: number }>('recommendations', filters, signal, { limit, offset })
export const getOperationalAvailability = (filters: OperationalFilter, signal?: AbortSignal) =>
  operationalGet<OperationalAvailability>('availability', filters, signal)

export async function reviewOperationalEvent(eventId: string, status: 'reviewed' | 'dismissed', note: string): Promise<OperationEvent> {
  const response = await fetch(`${API_BASE_URL}/api/operations/events/${encodeURIComponent(eventId)}/review`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, note: note.trim() || null, reviewer_identifier: null }),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<OperationEvent>
}

export async function getAIStatus(signal?: AbortSignal): Promise<AIStatus> {
  const response = await fetch(`${API_BASE_URL}/api/ai/status`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AIStatus>
}

export async function getAISummary(jobId: string, signal?: AbortSignal): Promise<AIReport | null> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/ai-summary`, { signal })
  if (response.status === 404) return null
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AIReport>
}

export async function generateAISummary(jobId: string, regenerate = false): Promise<AIReport> {
  const params = regenerate ? '?regenerate=true' : ''
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/ai-summary${params}`, { method: 'POST' })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AIReport>
}

export async function askAI(jobId: string, question: string): Promise<AIAnswer> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/ask-ai`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AIAnswer>
}

export async function startAnalysis(videoId: string, config: {
  confidence_threshold: number
  sampling_rate_fps: number
  image_size?: number
  tracking_enabled?: boolean
}): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE_URL}/api/videos/${encodeURIComponent(videoId)}/analyses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AnalysisJob>
}

export async function getAnalysisJob(jobId: string, signal?: AbortSignal): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AnalysisJob>
}

export async function getAnalysisJobs(signal?: AbortSignal): Promise<AnalysisJobListResponse> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs?limit=100&offset=0`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AnalysisJobListResponse>
}

export async function getAnalysisResults(jobId: string, signal?: AbortSignal): Promise<AnalysisResults> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/results?limit=100&offset=0`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<AnalysisResults>
}

export async function getAnalysisTracks(jobId: string, signal?: AbortSignal): Promise<TrackSummaryList> {
  const response = await fetch(`${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/tracks?limit=100&offset=0`, { signal })
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return response.json() as Promise<TrackSummaryList>
}

export function getAnnotatedVideoUrl(jobId: string): string {
  return `${API_BASE_URL}/api/analysis-jobs/${encodeURIComponent(jobId)}/annotated-video`
}

export function uploadVideo(
  file: File,
  idempotencyKey: string,
  onProgress: (progress: UploadProgress) => void,
): VideoUploadTask {
  const request = new XMLHttpRequest()
  let lastLoaded = 0
  let lastTotal = file.size
  const startedAt = performance.now()
  let stallTimer: ReturnType<typeof setTimeout> | undefined
  let responseTimer: ReturnType<typeof setTimeout> | undefined
  let abortReason: string | null = null
  const clearTimers = () => {
    if (stallTimer) clearTimeout(stallTimer)
    if (responseTimer) clearTimeout(responseTimer)
    stallTimer = undefined
    responseTimer = undefined
  }
  const resetStallTimer = () => {
    if (stallTimer) clearTimeout(stallTimer)
    stallTimer = setTimeout(() => {
      abortReason = 'Upload stalled because no bytes transferred for 2 minutes. Check your connection and retry.'
      request.abort()
    }, UPLOAD_STALL_TIMEOUT_MS)
  }
  const report = (progress: UploadProgress) => onProgress(progress)
  report({ loaded: 0, total: file.size, percent: 0, speedBytesPerSecond: null, remainingSeconds: null, phase: 'preparing' })
  const promise = new Promise<VideoRecord>((resolve, reject) => {
    try {
      request.open('POST', `${API_BASE_URL}/api/videos`)
      request.timeout = UPLOAD_TIMEOUT_MS
      request.setRequestHeader('Upload-Idempotency-Key', idempotencyKey)
    } catch (error) {
      reject(error instanceof Error ? error : new Error('The upload request could not be prepared.'))
      return
    }
    request.upload.onprogress = (event) => {
      resetStallTimer()
      if (!event.lengthComputable || event.total <= 0) return
      const now = performance.now()
      const elapsedSeconds = (now - startedAt) / 1000
      const speed = elapsedSeconds >= 0.5 ? event.loaded / elapsedSeconds : null
      lastLoaded = event.loaded
      lastTotal = event.total
      resetStallTimer()
      report({
        loaded: event.loaded,
        total: event.total,
        percent: Math.min(100, Math.round((event.loaded / event.total) * 100)),
        speedBytesPerSecond: speed,
        remainingSeconds: speed && speed > 0 ? (event.total - event.loaded) / speed : null,
        phase: 'uploading',
      })
    }
    request.upload.onload = () => {
      if (stallTimer) clearTimeout(stallTimer)
      stallTimer = undefined
      // Upload's load event confirms the entire multipart request body was sent.
      lastLoaded = lastTotal
      responseTimer = setTimeout(() => {
        abortReason = 'The server did not confirm the upload within 5 minutes. Check the video library before retrying.'
        request.abort()
      }, UPLOAD_RESPONSE_TIMEOUT_MS)
      report({
        loaded: lastLoaded,
        total: lastTotal,
        percent: lastTotal > 0 ? Math.min(100, Math.round(lastLoaded / lastTotal * 100)) : 0,
        speedBytesPerSecond: null,
        remainingSeconds: null,
        phase: 'server',
      })
    }
    request.onload = () => {
      clearTimers()
      let body: unknown
      try {
        body = JSON.parse(request.responseText) as unknown
      } catch {
        body = undefined
      }
      if (request.status >= 200 && request.status < 300 && typeof body === 'object' && body !== null && 'id' in body) {
        report({ loaded: lastLoaded, total: lastTotal, percent: 100, speedBytesPerSecond: null, remainingSeconds: 0, phase: 'completed' })
        resolve(body as VideoRecord)
        return
      }
      const detail = typeof body === 'object' && body !== null && 'detail' in body ? body.detail : undefined
      if (typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string') {
        reject(new Error(detail.message))
      } else if (typeof detail === 'string') {
        reject(new Error(detail))
      } else {
        reject(new Error(`Upload failed (${request.status || 'unknown status'})`))
      }
    }
    request.onerror = () => { clearTimers(); reject(new Error('Network error: the video service could not complete the upload. Check the library before retrying.')) }
    request.ontimeout = () => { clearTimers(); reject(new Error('Upload exceeded the 60-minute maximum duration. The server may have saved it; check the video library before retrying.')) }
    request.onabort = () => {
      clearTimers()
      reject(new Error(abortReason ?? 'Upload cancelled. Any incomplete temporary file was cleaned up; check the library before retrying.'))
    }
    try {
      const formData = new FormData()
      formData.append('file', file, file.name)
      resetStallTimer()
      request.send(formData)
    } catch (error) {
      clearTimers()
      reject(error instanceof Error ? error : new Error('The upload request could not be sent.'))
    }
  })
  return { promise, abort: () => request.abort() }
}
