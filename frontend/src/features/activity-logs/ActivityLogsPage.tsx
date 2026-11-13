import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertCircle, Bot, CheckCircle2, ChevronLeft, ChevronRight, Clock3,
  Download, FileClock, FilterX, LoaderCircle, Search, ShieldAlert, Video, X,
} from 'lucide-react'
import {
  type ActivityCategory, type ActivityFilterOptions, type ActivityLog, type ActivityQuery,
  type ActivitySeverity, type ActivityStatus, type ActivitySummary,
  exportActivityCsv, getActivityFilters, getActivityLog, getActivityLogs, getActivitySummary,
} from '../../lib/api'
import './ActivityLogsPage.css'

const PAGE_SIZE = 30
const RECENT_WINDOW_TEXT = 'last 24 hours'
const timestamp = (value: string) => new Date(value).toLocaleString()
const count = (value: number) => new Intl.NumberFormat().format(value)

export default function ActivityLogsPage({ onNavigate }: { onNavigate: (page: string) => void }) {
  const [options, setOptions] = useState<ActivityFilterOptions | null>(null)
  const [category, setCategory] = useState<ActivityCategory | ''>('')
  const [severity, setSeverity] = useState<ActivitySeverity | ''>('')
  const [status, setStatus] = useState<ActivityStatus | ''>('')
  const [eventType, setEventType] = useState('')
  const [source, setSource] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<{ items: ActivityLog[]; total: number; limit: number; offset: number } | null>(null)
  const [summary, setSummary] = useState<ActivitySummary | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [selected, setSelected] = useState<ActivityLog | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [exportError, setExportError] = useState('')
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    void getActivityFilters(controller.signal).then(setOptions).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Activity filters could not be loaded.')
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const filters = useMemo<ActivityQuery>(() => ({
    ...(category ? { category } : {}), ...(severity ? { severity } : {}), ...(status ? { status } : {}),
    ...(eventType ? { event_type: eventType } : {}), ...(source ? { source } : {}),
    ...(dateFrom ? { date_from: `${dateFrom}T00:00:00Z` } : {}),
    ...(dateTo ? { date_to: `${dateTo}T23:59:59.999Z` } : {}),
    ...(debouncedSearch ? { search: debouncedSearch } : {}),
  }), [category, severity, status, eventType, source, dateFrom, dateTo, debouncedSearch])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    Promise.all([
      getActivityLogs(filters, controller.signal, PAGE_SIZE, offset),
      getActivitySummary(filters, controller.signal),
    ]).then(([logPage, totals]) => {
      setPage(logPage); setSummary(totals)
      if (!selectedId && logPage.items.length) setSelectedId(logPage.items[0].id)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Activity records could not be loaded.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filters, offset, retry])

  useEffect(() => {
    if (!selectedId) { setSelected(null); return }
    const controller = new AbortController()
    setDetailLoading(true)
    void getActivityLog(selectedId, controller.signal).then(setSelected).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Activity details could not be loaded.')
    }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [selectedId, retry])

  const clearFilters = () => {
    setCategory(''); setSeverity(''); setStatus(''); setEventType(''); setSource('')
    setDateFrom(''); setDateTo(''); setSearch(''); setOffset(0)
  }

  const exportCsv = async () => {
    setExporting(true); setExportError('')
    try {
      const blob = await exportActivityCsv(filters)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = 'vanguard-activity.csv'; anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : 'The CSV export could not be created.')
    } finally { setExporting(false) }
  }

  return <div className="activity-page">
    <div className="page-heading-row">
      <div><div className="overline"><span className="overline-line" />TRACEABLE SYSTEM HISTORY</div><h1>AI Activity Logs</h1><p className="page-subtitle">A searchable timeline of actual video, analysis, reasoning and operational activity.</p></div>
      <button className="activity-export" onClick={() => void exportCsv()} disabled={exporting}><Download size={15} />{exporting ? 'Preparing CSV…' : 'Export CSV'}</button>
    </div>

    {exportError && <div className="activity-inline-error" role="alert"><AlertCircle size={15} />{exportError}<button onClick={() => setExportError('')} aria-label="Dismiss export error"><X size={14} /></button></div>}
    {error && <div className="activity-inline-error" role="alert"><AlertCircle size={15} /><span>{error}</span><button onClick={() => { setError(''); setRetry((n) => n + 1) }}>Retry</button></div>}

    <section className="activity-summary-grid" aria-label="Activity summary">
      <Summary icon={<Activity size={17} />} label="TOTAL EVENTS" value={summary?.total_events} />
      <Summary icon={<CheckCircle2 size={17} />} label="SUCCESSFUL" value={summary?.successful_executions} />
      <Summary icon={<ShieldAlert size={17} />} label="FAILED" value={summary?.failed_executions} />
      <Summary icon={<AlertCircle size={17} />} label="WARNINGS" value={summary?.warning_events} />
      <Summary icon={<Clock3 size={17} />} label="RECENT ACTIVITY" value={summary?.recent_events} note={`in the ${RECENT_WINDOW_TEXT}`} />
    </section>

    <section className="panel activity-filter-panel">
      <div className="activity-filter-heading"><div><strong>Filter activity</strong><span>Records are ordered by event time.</span></div><button onClick={clearFilters}><FilterX size={14} />Reset</button></div>
      <div className="activity-filter-grid">
        <label>Search title or description<span className="activity-search"><Search size={14} /><input value={search} onChange={(event) => { setSearch(event.target.value); setOffset(0) }} placeholder="Search activity" /></span></label>
        <label>Category<select value={category} onChange={(event) => { setCategory(event.target.value as ActivityCategory | ''); setOffset(0) }}><option value="">All categories</option>{(options?.categories ?? []).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Event type<select value={eventType} onChange={(event) => { setEventType(event.target.value); setOffset(0) }}><option value="">All event types</option>{(options?.event_types ?? []).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Severity<select value={severity} onChange={(event) => { setSeverity(event.target.value as ActivitySeverity | ''); setOffset(0) }}><option value="">All severity</option>{(options?.severities ?? []).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value as ActivityStatus | ''); setOffset(0) }}><option value="">All statuses</option>{(options?.statuses ?? []).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Source<select value={source} onChange={(event) => { setSource(event.target.value); setOffset(0) }}><option value="">All sources</option>{(options?.sources ?? []).map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>From<input type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); setOffset(0) }} /></label>
        <label>Through<input type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); setOffset(0) }} /></label>
      </div>
    </section>

    <section className="activity-content-grid">
      <section className="panel activity-feed-panel">
        <div className="activity-panel-heading"><div><h2>Activity timeline</h2><p>{page ? `${count(page.total)} matching records` : 'Loading records'}</p></div><span className="activity-live"><span />PERSISTED ACTIVITY</span></div>
        {loading && !page ? <div className="activity-state"><LoaderCircle className="activity-spin" size={18} />Loading activity records…</div> : null}
        {!loading && page?.items.length === 0 && <div className="activity-state"><FileClock size={19} /><span>No real activity matches these filters.</span></div>}
        {page?.items.length ? <div className="activity-feed">{page.items.map((item) => <button key={item.id} className={`activity-feed-row ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
          <span className={`activity-row-icon ${item.category.toLowerCase()}`}><CategoryIcon category={item.category} /></span>
          <span className="activity-row-main"><span><strong>{item.title}</strong><small className={`activity-status ${item.status.toLowerCase()}`}>{item.status}</small></span><span className="activity-row-description">{item.description}</span><span className="activity-row-meta"><span className={`activity-severity ${item.severity.toLowerCase()}`}>{item.severity}</span><span>{item.category}</span><span>{item.source}</span>{item.duration_ms != null && <span>{item.duration_ms.toFixed(0)} ms</span>}</span></span>
          <time>{timestamp(item.timestamp)}</time>
        </button>)}</div> : null}
        {page && page.total > PAGE_SIZE && <div className="activity-pagination"><span>{offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {count(page.total)}</span><div><button aria-label="Previous page" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={15} /></button><button aria-label="Next page" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}><ChevronRight size={15} /></button></div></div>}
      </section>

      <aside className="panel activity-detail-panel" aria-label="Selected activity details">
        <div className="activity-panel-heading"><div><h2>Activity details</h2><p>{selected ? selected.id : 'Select a timeline record'}</p></div>{selected && <button className="activity-close-detail" onClick={() => setSelectedId('')} aria-label="Clear selected activity"><X size={15} /></button>}</div>
        {detailLoading && <div className="activity-state"><LoaderCircle className="activity-spin" size={17} />Loading details…</div>}
        {!detailLoading && !selected && <div className="activity-state">Select an activity to inspect its structured details.</div>}
        {selected && <div className="activity-detail">
          <div className="activity-detail-badges"><span className={`activity-severity ${selected.severity.toLowerCase()}`}>{selected.severity}</span><span className={`activity-status ${selected.status.toLowerCase()}`}>{selected.status}</span><span>{selected.category}</span></div>
          <h3>{selected.title}</h3><p>{selected.description}</p>
          <Detail label="Event type" value={selected.event_type} /><Detail label="Timestamp" value={timestamp(selected.timestamp)} /><Detail label="Source" value={selected.source} />
          {selected.duration_ms != null && <Detail label="Duration" value={`${selected.duration_ms.toFixed(1)} ms`} />}
          {selected.error_type && <Detail label="Error classification" value={selected.error_type} />}
          <Related label="Video" value={selected.video_id} onOpen={() => onNavigate('Video Intelligence')} />
          <Related label="Analysis job" value={selected.analysis_job_id} onOpen={() => onNavigate('Video Intelligence')} />
          <Related label="Workflow" value={selected.workflow_id} onOpen={() => onNavigate('Vision Agents')} />
          <Related label="Agent" value={selected.agent_id} onOpen={() => onNavigate('Vision Agents')} />
          <Related label="Operational event" value={selected.operational_event_id} onOpen={() => onNavigate('Operational Insights')} />
          <div className="activity-metadata"><strong>Structured metadata</strong><pre>{JSON.stringify(selected.metadata, null, 2)}</pre></div>
        </div>}
      </aside>
    </section>
    <footer className="page-footer"><span>VANGUARD AI <i>·</i> APPEND-ONLY ACTIVITY HISTORY</span><span>RECENT WINDOW: LAST 24 HOURS</span></footer>
  </div>
}

function Summary({ icon, label, value, note }: { icon: React.ReactNode; label: string; value?: number; note?: string }) {
  return <article className="panel activity-summary-card"><div><span>{icon}</span><small>{label}</small></div><strong>{value == null ? '—' : count(value)}</strong><p>{note ?? 'Filtered persisted records'}</p></article>
}
function Detail({ label, value }: { label: string; value: string }) { return <div className="activity-detail-field"><span>{label}</span><strong>{value}</strong></div> }
function Related({ label, value, onOpen }: { label: string; value: string | null; onOpen: () => void }) {
  if (!value) return null
  return <div className="activity-detail-field"><span>{label}</span><button onClick={onOpen}>{value}</button></div>
}
function CategoryIcon({ category }: { category: ActivityCategory }) {
  if (category === 'VIDEO') return <Video size={15} />
  if (category === 'AGENT' || category === 'WORKFLOW') return <Bot size={15} />
  if (category === 'SECURITY') return <ShieldAlert size={15} />
  return <Activity size={15} />
}
