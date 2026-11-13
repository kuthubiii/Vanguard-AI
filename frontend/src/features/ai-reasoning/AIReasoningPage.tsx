import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertCircle, CheckCircle2, LoaderCircle, MessageSquareText, RefreshCw, Sparkles } from 'lucide-react'
import './AIReasoningPage.css'
import {
  type AIAnswer, type AIReport, type AIStatus, type AnalysisJob,
  askAI, generateAISummary, getAIStatus, getAISummary, getAnalysisJobs,
} from '../../lib/api'

function formatTime(seconds: number) {
  const mins = Math.floor(seconds / 60)
  return `${mins}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

export default function AIReasoningPage() {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [jobs, setJobs] = useState<AnalysisJob[]>([])
  const [jobId, setJobId] = useState('')
  const [report, setReport] = useState<AIReport | null>(null)
  const [booting, setBooting] = useState(true)
  const [reportLoading, setReportLoading] = useState(false)
  const [error, setError] = useState('')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<AIAnswer | null>(null)
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState('')

  useEffect(() => {
    let active = true
    Promise.allSettled([getAIStatus(), getAnalysisJobs()]).then(([statusResult, jobsResult]) => {
      if (!active) return
      if (statusResult.status === 'fulfilled') setStatus(statusResult.value)
      if (jobsResult.status === 'fulfilled') {
        const completed = jobsResult.value.items.filter((job) => job.status === 'completed')
        setJobs(completed)
        if (completed.length) setJobId(completed[0].job_id)
      } else setError(jobsResult.reason instanceof Error ? jobsResult.reason.message : 'Completed analyses could not be loaded.')
      setBooting(false)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!jobId) { setReport(null); return }
    let active = true
    setReportLoading(true)
    setError('')
    setAnswer(null)
    getAISummary(jobId).then((value) => {
      if (active) setReport(value)
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'The saved AI report could not be loaded.')
    }).finally(() => { if (active) setReportLoading(false) })
    return () => { active = false }
  }, [jobId])

  const selectedJob = jobs.find((job) => job.job_id === jobId)
  const detectionsById = useMemo(() => new Map((report?.evidence.representative_detections ?? []).map((item) => [item.detection_id, item])), [report])
  const ready = status?.connected === true && status.model_available && status.inference_ready

  const generate = async (regenerate = false) => {
    if (!jobId || !ready || reportLoading) return
    setReportLoading(true)
    setError('')
    try { setReport(await generateAISummary(jobId, regenerate)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The local model could not generate a report.') }
    finally { setReportLoading(false) }
  }

  const submitQuestion = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!jobId || !question.trim() || !ready || asking) return
    setAsking(true)
    setAskError('')
    try { setAnswer(await askAI(jobId, question.trim())) }
    catch (reason) { setAskError(reason instanceof Error ? reason.message : 'The local model could not answer this question.') }
    finally { setAsking(false) }
  }

  return <div className="ai-reasoning-page">
    <div className="page-heading-row">
      <div><div className="overline"><span className="overline-line" />LOCAL INTELLIGENCE</div><h1>AI Reasoning</h1><p className="page-subtitle">Evidence-led summaries and operational observations from completed video analyses.</p></div>
      <div className={`ai-provider-pill ${ready ? 'ready' : 'offline'}`}><span />{status?.model ?? 'Checking local model'}</div>
    </div>

    <section className={`panel ai-status-card ${ready ? 'is-ready' : ''}`} aria-live="polite">
      <div className="ai-status-icon">{ready ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}</div>
      <div className="ai-status-copy"><strong>{booting ? 'Checking LM Studio local server' : ready ? 'LM Studio inference ready' : status?.status === 'model_missing' ? 'Configured model ID is unavailable' : status?.status === 'inference_unready' ? 'Model is listed, but inference is not ready' : 'LM Studio is unavailable'}</strong>
        <p>{booting ? 'Checking the configured local endpoint and model.' : status?.message ?? 'Could not retrieve local AI status.'}</p>
        {!booting && !ready && <small>Open LM Studio, start its local server at <code>{status?.endpoint ?? 'http://127.0.0.1:1235/v1'}</code>, and load <code>{status?.model ?? 'qwen3-4b-instruct-2507'}</code>.</small>}
      </div>
      <button className="secondary-button ai-refresh" onClick={() => getAIStatus().then(setStatus).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Status check failed.'))} aria-label="Refresh LM Studio status"><RefreshCw size={14} />Refresh</button>
    </section>

    <section className="panel ai-job-panel">
      <div className="ai-panel-heading"><div><h2>Source analysis</h2><p>Reasoning uses persisted detections and tracking records only.</p></div><label className="ai-job-select">COMPLETED JOB
        <select value={jobId} onChange={(event) => setJobId(event.target.value)} disabled={booting || jobs.length === 0} aria-label="Select completed analysis job">
          {jobs.length === 0 && <option value="">No completed analyses</option>}
          {jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.job_id.slice(0, 8)} · {job.detections_count.toLocaleString()} detections</option>)}
        </select>
      </label></div>
      {selectedJob && <div className="ai-job-meta"><span>{selectedJob.status}</span><span>{selectedJob.frames_sampled.toLocaleString()} sampled frames</span><span>{selectedJob.detections_count.toLocaleString()} detections</span><span>{selectedJob.tracking_enabled ? 'Tracking enabled' : 'Tracking disabled'}</span></div>}
    </section>

    {error && <div className="ai-error" role="alert"><AlertCircle size={16} />{error}</div>}
    {!jobId && !booting && <section className="panel ai-empty"><Sparkles size={22} /><strong>No completed analysis available</strong><p>Complete a video analysis before requesting evidence-based reasoning.</p></section>}

    {jobId && <>
      <section className="panel ai-report-panel">
        <div className="ai-panel-heading"><div><h2>Analysis summary</h2><p>{report ? `Generated by ${report.generated_by} · ${new Date(report.generated_at).toLocaleString()}` : 'No report saved for this job yet.'}</p></div>
          <button className="primary-button" onClick={() => void generate(Boolean(report))} disabled={!ready || reportLoading || booting}>
            {reportLoading ? <><LoaderCircle className="spin" size={15} />Reasoning…</> : <><Sparkles size={15} />{report ? 'Regenerate summary' : 'Generate AI summary'}</>}
          </button>
        </div>
        {reportLoading && <p className="ai-inline-status" role="status">Waiting for the local model. This may take a few minutes on the first request.</p>}
        {report && <>
          <p className="ai-summary-text">{report.summary}</p>
          <div className="ai-evidence-stats">
            <div><strong>{report.evidence.detection_count.toLocaleString()}</strong><span>Persisted detections</span></div>
            <div><strong>{report.evidence.unique_track_count.toLocaleString()}</strong><span>Unique tracks</span></div>
            {report.evidence.class_counts.map((item) => <div key={item.class_id}><strong>{item.count.toLocaleString()}</strong><span>{item.class_name}</span></div>)}
          </div>
        </>}
      </section>

      {report && <div className="ai-results-grid">
        <section className="panel ai-result-panel"><h2>Observations <span>{report.observations.length}</span></h2>
          {report.observations.length === 0 ? <p className="ai-muted">No supported observations were returned.</p> : report.observations.map((item, index) => <article className="ai-result-item" key={`${item.title}-${index}`}><div className="ai-result-title"><strong>{item.title}</strong><span className={`confidence-${item.confidence}`}>{item.confidence}</span></div><p>{item.description}</p>{item.evidence.map((ref) => <EvidenceChip key={ref.detection_id} reference={ref} detection={detectionsById.get(ref.detection_id)} />)}{item.limitations && <small>Limitations: {item.limitations}</small>}</article>)}
        </section>
        <section className="panel ai-result-panel"><h2>Recommendations <span>{report.recommendations.length}</span></h2>
          {report.recommendations.length === 0 ? <p className="ai-muted">No recommendations were returned.</p> : report.recommendations.map((item, index) => <article className="ai-result-item" key={`${item.title}-${index}`}><div className="ai-result-title"><strong>{item.title}</strong><span className="human-review">Human review</span></div><p>{item.description}</p><small>{item.reason}</small>{item.evidence.map((ref) => <EvidenceChip key={ref.detection_id} reference={ref} detection={detectionsById.get(ref.detection_id)} />)}</article>)}
        </section>
      </div>}

      {report && <section className="panel ai-limits"><h2>Limitations and uncertainty</h2><ul>{report.limitations.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul><p>YOLO26n uses COCO classes. It does not independently detect warehouse hazards, damaged packages, or policy violations.</p></section>}

      <section className="panel ai-chat-panel"><div className="ai-panel-heading"><div><h2><MessageSquareText size={16} /> Ask AI about this analysis</h2><p>Answers are grounded in this job’s persisted evidence.</p></div></div>
        <form className="ai-chat-form" onSubmit={(event) => void submitQuestion(event)}><input value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={1000} placeholder="What object classes were detected?" aria-label="Question about selected analysis" /><button className="primary-button" disabled={!ready || asking || question.trim().length < 3}>{asking ? <><LoaderCircle className="spin" size={15} />Thinking…</> : 'Ask'}</button></form>
        {askError && <div className="ai-error compact" role="alert">{askError}</div>}
        {answer && <article className="ai-answer"><p>{answer.answer}</p>{answer.evidence.map((ref) => <EvidenceChip key={ref.detection_id} reference={ref} detection={detectionsById.get(ref.detection_id)} />)}{answer.limitations.map((item, index) => <small key={`${item}-${index}`}>{item}</small>)}</article>}
      </section>
    </>}
    <footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL REASONING</span><span>SEE. THINK. PREDICT. ACT.</span></footer>
  </div>
}

function EvidenceChip({ reference, detection }: { reference: AIReport['observations'][number]['evidence'][number]; detection?: AIReport['evidence']['representative_detections'][number] }) {
  return <span className="ai-evidence-chip">{detection?.class_name ?? 'Evidence'}{reference.track_id === null ? '' : ` · Track ${reference.track_id}`} · {formatTime(reference.timestamp)}</span>
}
