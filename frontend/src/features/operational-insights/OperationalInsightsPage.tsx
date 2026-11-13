import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Activity, ArrowDown, ArrowUpRight, Check, CircleHelp, LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  type OperationalAnalytics, type OperationalAvailability, type OperationalFilter, type OperationalOverview,
  type OperationalRecommendation, type OperationalRisk, type OperationEvent, getAnalysisJobs,
  getOperationalAnalytics, getOperationalAvailability, getOperationalEvents, getOperationalOverview,
  getOperationalRecommendations, getOperationalRisks, reviewOperationalEvent,
} from '../../lib/api'
import './OperationalInsightsPage.css'

const PAGE_SIZE = 8
const colors = ['#c2ee67', '#91c4a3', '#85bad1', '#c0a4dc', '#e5bd71', '#dc8b78', '#8dd8d1']
const dateLabel = (value: string) => new Date(value).toLocaleString()
const number = (value: number) => new Intl.NumberFormat().format(value)

export default function OperationalInsightsPage() {
  const [jobOptions, setJobOptions] = useState<{ job_id: string; video_id: string; video_filename?: string }[]>([])
  const [jobId, setJobId] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [eventType, setEventType] = useState('')
  const [severity, setSeverity] = useState('')
  const [reviewStatus, setReviewStatus] = useState('pending')
  const [overview, setOverview] = useState<OperationalOverview | null>(null)
  const [analytics, setAnalytics] = useState<OperationalAnalytics | null>(null)
  const [events, setEvents] = useState<{ items: OperationEvent[]; total: number; limit: number; offset: number } | null>(null)
  const [risks, setRisks] = useState<{ items: OperationalRisk[]; total: number; limit: number; offset: number } | null>(null)
  const [recommendations, setRecommendations] = useState<{ items: OperationalRecommendation[]; total: number; limit: number; offset: number } | null>(null)
  const [availability, setAvailability] = useState<OperationalAvailability | null>(null)
  const [eventOffset, setEventOffset] = useState(0)
  const [trackOffset, setTrackOffset] = useState(0)
  const [riskOffset, setRiskOffset] = useState(0)
  const [recommendationOffset, setRecommendationOffset] = useState(0)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [savingEvent, setSavingEvent] = useState('')
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)

  const filters = useMemo<OperationalFilter>(() => ({
    ...(jobId ? { analysis_job_id: jobId } : {}),
    ...(dateFrom ? { date_from: `${dateFrom}T00:00:00Z` } : {}),
    ...(dateTo ? { date_to: `${dateTo}T23:59:59.999Z` } : {}),
  }), [jobId, dateFrom, dateTo])

  useEffect(() => {
    let active = true
    void getAnalysisJobs().then((result) => {
      if (active) setJobOptions(result.items.filter((job) => job.status === 'completed').map((job) => ({
        job_id: job.job_id, video_id: job.video_id,
      })))
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Completed analyses could not be loaded.')
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    Promise.all([
      getOperationalOverview(filters, controller.signal),
      getOperationalAnalytics(filters, controller.signal, PAGE_SIZE, trackOffset),
      getOperationalEvents(filters, controller.signal, PAGE_SIZE, eventOffset, reviewStatus || undefined, eventType || undefined, severity || undefined),
      getOperationalRisks(filters, controller.signal, PAGE_SIZE, riskOffset),
      getOperationalRecommendations(filters, controller.signal, PAGE_SIZE, recommendationOffset),
      getOperationalAvailability(filters, controller.signal),
    ]).then(([overviewData, analyticsData, eventsData, risksData, recommendationsData, availabilityData]) => {
      setOverview(overviewData); setAnalytics(analyticsData); setEvents(eventsData)
      setRisks(risksData); setRecommendations(recommendationsData); setAvailability(availabilityData)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Operational insights could not be loaded.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filters, eventOffset, trackOffset, riskOffset, recommendationOffset, reviewStatus, eventType, severity, retry])

  const review = async (event: OperationEvent, status: 'reviewed' | 'dismissed') => {
    setSavingEvent(event.event_id); setError('')
    try {
      await reviewOperationalEvent(event.event_id, status, notes[event.event_id] ?? '')
      setEventOffset(0)
      setRetry((current) => current + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The review decision could not be saved.')
    } finally { setSavingEvent('') }
  }

  const setJobFilter = (nextJobId: string) => {
    setJobId(nextJobId); setEventOffset(0); setTrackOffset(0); setRiskOffset(0); setRecommendationOffset(0)
  }

  const clearFilters = () => {
    setJobId(''); setDateFrom(''); setDateTo(''); setEventType(''); setSeverity('')
    setEventOffset(0); setTrackOffset(0); setRiskOffset(0); setRecommendationOffset(0)
  }

  return <div className="operations-page">
    <div className="page-heading-row">
      <div><div className="overline"><span className="overline-line" />EVIDENCE-BASED WAREHOUSE INTELLIGENCE</div><h1>Operational Insights</h1><p className="page-subtitle">Patterns and review cues from persisted video analysis.</p></div>
      <div className="operations-source-note"><span /> RULES + STORED EVIDENCE</div>
    </div>

    <section className="panel operations-filters">
      <div className="operations-filter-head"><div><strong>Analysis scope</strong><span>Dates refer to analysis creation time (UTC).</span></div><button className="operations-clear" onClick={clearFilters}>Clear filters</button></div>
      <div className="operations-filter-grid">
        <label>Analysis job<select value={jobId} onChange={(event) => setJobFilter(event.target.value)}><option value="">All completed jobs</option>{jobOptions.map((job) => <option key={job.job_id} value={job.job_id}>{job.job_id.slice(0, 8)} · {job.video_id.slice(0, 8)}</option>)}</select></label>
        <label>Created from<input type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); setEventOffset(0); setTrackOffset(0) }} /></label>
        <label>Created through<input type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); setEventOffset(0); setTrackOffset(0) }} /></label>
        <label>Event type<select value={eventType} onChange={(event) => { setEventType(event.target.value); setEventOffset(0) }}><option value="">All event types</option><option value="high_detection_activity">High detection activity</option><option value="low_detection_activity">Low detection activity</option><option value="category_distribution_change">Category distribution change</option><option value="frame_observation_concentration">Frame observation concentration</option><option value="repeated_track_observations">Repeated track observations</option></select></label>
        <label>Severity<select value={severity} onChange={(event) => { setSeverity(event.target.value); setEventOffset(0) }}><option value="">All severities</option><option value="informational">Informational</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
        <label>Review status<select value={reviewStatus} onChange={(event) => { setReviewStatus(event.target.value); setEventOffset(0) }}><option value="pending">Pending review</option><option value="reviewed">Reviewed</option><option value="dismissed">Dismissed</option><option value="">All statuses</option></select></label>
      </div>
    </section>

    {error && <div className="operations-error" role="alert"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setRetry((value) => value + 1)}><RefreshCw size={14} /> Retry</button></div>}
    {loading && !overview ? <section className="panel operations-loading"><LoaderCircle className="operations-spin" size={21} /> Loading stored operational evidence…</section> : null}

    {overview && <>
      <section className="operations-metric-grid">
        <Metric icon={<Activity size={17} />} label="ANALYSED VIDEOS" value={overview.metrics.total_videos_analysed} note="Videos with a completed analysis" />
        <Metric icon={<Check size={17} />} label="COMPLETED JOBS" value={overview.metrics.completed_analysis_jobs} note="Completed analysis records" />
        <Metric icon={<ArrowUpRight size={17} />} label="DETECTIONS" value={overview.metrics.total_detected_objects} note="Persisted detector observations" />
        <Metric icon={<CircleHelp size={17} />} label="TRACKED ENTITIES" value={overview.metrics.total_unique_tracked_entities} note="Track IDs scoped to each job" />
        <Metric icon={<ShieldAlert size={17} />} label="PENDING REVIEW" value={overview.metrics.events_requiring_attention} note="Rule-based items awaiting a person" />
      </section>
      <section className="operations-two-column">
        <article className="panel operations-chart-panel"><PanelTitle title="Detection activity by analysis date" subtitle="Counts use persisted detections, grouped by UTC analysis creation date." />
          {overview.detection_trend.length ? <div className="operations-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={overview.detection_trend}><CartesianGrid stroke="#252b29" strokeDasharray="3 4" /><XAxis dataKey="bucket" tick={{ fill: '#89928c', fontSize: 10 }} /><YAxis tick={{ fill: '#89928c', fontSize: 10 }} /><Tooltip contentStyle={{ background: '#151a17', border: '1px solid #343d34', borderRadius: 8, color: '#e1e8db' }} /><Line type="monotone" dataKey="detection_count" name="Detections" stroke="#c2ee67" strokeWidth={2} dot={{ r: 3 }} /><Line type="monotone" dataKey="tracked_detection_count" name="Tracked detections" stroke="#78c6b7" strokeWidth={2} dot={{ r: 2 }} /></LineChart></ResponsiveContainer></div> : <Empty text="No completed analysis activity in this date range." />}
        </article>
        <article className="panel operations-chart-panel"><PanelTitle title="Object category distribution" subtitle="Detections and tracked entities are reported separately." />
          {overview.category_distribution.length ? <div className="operations-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={overview.category_distribution} layout="vertical" margin={{ left: 12, right: 12 }}><CartesianGrid stroke="#252b29" strokeDasharray="3 4" /><XAxis type="number" tick={{ fill: '#89928c', fontSize: 10 }} /><YAxis type="category" dataKey="class_name" width={75} tick={{ fill: '#aab3a8', fontSize: 10 }} /><Tooltip contentStyle={{ background: '#151a17', border: '1px solid #343d34', borderRadius: 8, color: '#e1e8db' }} /><Bar dataKey="detection_count" name="Detections" radius={[0, 4, 4, 0]}>{overview.category_distribution.map((item, index) => <Cell key={item.class_id} fill={colors[index % colors.length]} />)}</Bar></BarChart></ResponsiveContainer></div> : <Empty text="No detections are available in this scope." />}
          <div className="operations-categories">Tracked categories: {overview.metrics.tracked_object_categories.length ? overview.metrics.tracked_object_categories.join(', ') : 'none available'}</div>
        </article>
      </section>

      <section className="panel operations-section"><PanelTitle title="Object and tracking analytics" subtitle="Tracks are scoped to one analysis; sparse observations do not prove continuous presence." />
        <div className="operations-two-column operations-chart-pair">
          <div><h3>Confidence distribution</h3>{analytics?.confidence_distribution.some((item) => item.detection_count) ? <div className="operations-small-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={analytics.confidence_distribution}><CartesianGrid stroke="#252b29" strokeDasharray="3 4" /><XAxis dataKey="confidence_range" tick={{ fill: '#89928c', fontSize: 9 }} /><YAxis tick={{ fill: '#89928c', fontSize: 9 }} /><Tooltip contentStyle={{ background: '#151a17', border: '1px solid #343d34', borderRadius: 8, color: '#e1e8db' }} /><Bar dataKey="detection_count" name="Detections" fill="#9fc98c" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div> : <Empty text="No confidence values are available." />}</div>
          <div><h3>Analysis comparison</h3>{analytics?.job_comparisons.length ? <div className="operations-comparison-list">{analytics.job_comparisons.slice(0, 8).map((job) => <button className="comparison-row" key={job.analysis_job_id} onClick={() => setJobFilter(job.analysis_job_id)}><span><strong>{job.video_filename}</strong><small>{job.analysis_job_id.slice(0, 12)} · {dateLabel(job.created_at)}</small></span><span><b>{number(job.detection_count)}</b><small>detections</small></span><span><b>{number(job.unique_tracked_entities)}</b><small>tracks</small></span></button>)}</div> : <Empty text="No completed analyses match this filter." />}</div>
        </div>
        <div className="operations-table-wrap"><table className="operations-table"><thead><tr><th>Video</th><th>Analysis job</th><th>Track ID</th><th>Category</th><th>Observations</th><th>Observed time range</th></tr></thead><tbody>{analytics?.tracking_activity.map((track) => <tr key={`${track.analysis_job_id}:${track.track_id}:${track.class_id}`}><td>{track.video_filename}</td><td><button className="operations-link" onClick={() => setJobFilter(track.analysis_job_id)}>{track.analysis_job_id.slice(0, 12)}</button></td><td>{track.track_id}</td><td>{track.class_name}</td><td>{number(track.observation_count)}</td><td>{track.first_observed_seconds.toFixed(1)}s–{track.last_observed_seconds.toFixed(1)}s</td></tr>)}</tbody></table>{!analytics?.tracking_activity.length && <Empty text="No tracked observations are available." />}</div>
        {analytics && <Pagination total={analytics.tracking_total} offset={trackOffset} limit={PAGE_SIZE} onPage={setTrackOffset} />}
      </section>

      <section className="panel operations-section"><PanelTitle title="Operational events" subtitle="Rules describe observed data patterns. They do not confirm incidents or safety violations." />
        {events?.items.length ? <div className="operations-event-list">{events.items.map((event) => <article className="operations-event-card" key={event.event_id}>
          <div className="operations-event-head"><span className={`operations-severity ${event.severity}`}>{event.severity}</span><span className={`operations-review-status ${event.review_status}`}>{event.review_status} review</span><time>{dateLabel(event.event_timestamp)}</time></div>
          <h3>{event.title}</h3><p>{event.description}</p><div className="event-rule"><strong>Rule basis</strong><span>{event.evidence_explanation}</span><code>{String(event.rule_basis.rule ?? 'rule')}</code></div>
          <div className="event-evidence"><span>Job <button className="operations-link" onClick={() => setJobFilter(event.analysis_job_id)}>{event.analysis_job_id}</button></span><span>Video {event.video_id}</span>{event.evidence.timestamp_seconds != null && <span>Video time {event.evidence.timestamp_seconds.toFixed(2)}s</span>}{event.evidence.class_name && <span>Category {event.evidence.class_name}</span>}</div>
          {event.evidence.detection_ids.length > 0 && <div className="event-reference-list"><strong>Persisted detection references</strong><div>{event.evidence.detection_ids.slice(0, 8).map((id) => <code key={id}>{id}</code>)}</div></div>}
          {event.review_status === 'pending' && <div className="event-review-controls"><label htmlFor={`note-${event.event_id}`}>Review note (optional)</label><textarea id={`note-${event.event_id}`} value={notes[event.event_id] ?? ''} maxLength={2000} onChange={(e) => setNotes((current) => ({ ...current, [event.event_id]: e.target.value }))} placeholder="Record why this was reviewed or dismissed." /><div><button className="review-button" disabled={savingEvent === event.event_id} onClick={() => void review(event, 'reviewed')}>{savingEvent === event.event_id ? <LoaderCircle className="operations-spin" size={14} /> : <Check size={14} />} Mark reviewed</button><button className="dismiss-button" disabled={savingEvent === event.event_id} onClick={() => void review(event, 'dismissed')}>Dismiss with note</button></div></div>}
          {event.review_status !== 'pending' && <div className="review-history">Reviewed {event.reviewed_at ? dateLabel(event.reviewed_at) : ''}{event.reviewer_identifier ? ` by ${event.reviewer_identifier}` : ''}{event.review_note ? ` · ${event.review_note}` : ''}</div>}
        </article>)}</div> : <Empty text={loading ? 'Refreshing rule evaluation…' : 'No operational events match this filter.'} />}
        {events && <Pagination total={events.total} offset={eventOffset} limit={PAGE_SIZE} onPage={setEventOffset} />}
      </section>

      <section className="operations-two-column">
        <article className="panel operations-section"><PanelTitle title="Risk and attention indicators" subtitle="All indicators require human review and are not confirmed incidents." />
          {risks?.items.length ? <div className="operations-risk-list">{risks.items.map((risk) => <div className="operations-risk-card" key={risk.event_id}><div><ShieldAlert size={16} /><span className={`operations-severity ${risk.severity}`}>{risk.severity}</span><strong>{risk.risk_category.replaceAll('_', ' ')}</strong></div><h3>{risk.title}</h3><p>{risk.evidence_summary}</p><small>Rule: {String(risk.rule_triggered.rule ?? '')}</small><p className="risk-action">Suggested review: {risk.suggested_next_action}</p><button className="operations-link" onClick={() => setJobFilter(risk.analysis_job_id)}>Filter to source analysis <ArrowDown size={13} /></button></div>)}</div> : <Empty text="No supported rule indicators match this filter." />}
          {risks && <Pagination total={risks.total} offset={riskOffset} limit={PAGE_SIZE} onPage={setRiskOffset} />}
        </article>
        <article className="panel operations-section"><PanelTitle title="Recommendations" subtitle="Rule-based suggestions work without LM Studio. Existing AI suggestions are shown only when already persisted." />
          {recommendations?.items.length ? <div className="operations-recommendations">{recommendations.items.map((item) => <div className="operations-recommendation" key={item.recommendation_id}><div><span className={`recommendation-priority ${item.priority}`}>{item.priority} priority</span><span className="recommendation-source">{item.source === 'ai_report' ? 'Saved AI report' : 'Rule-based'}</span></div><h3>{item.title}</h3><p>{item.description}</p><small>{item.reason}</small>{item.analysis_job_id && <button className="operations-link" onClick={() => setJobFilter(item.analysis_job_id!)}>Open source analysis · {item.analysis_job_id.slice(0, 12)} <ArrowUpRight size={13} /></button>}{item.video_id && <small>Video {item.video_id}</small>}{item.evidence_references.length > 0 && <small>{item.evidence_references.length} persisted evidence references</small>}</div>)}</div> : <Empty text="No pending rule recommendations or saved AI recommendations are available." />}
          {recommendations && <Pagination total={recommendations.total} offset={recommendationOffset} limit={PAGE_SIZE} onPage={setRecommendationOffset} />}
        </article>
      </section>

      <section className="panel operations-section"><PanelTitle title="Capability availability" subtitle="Generic object detections do not establish damage, safety, location, or equipment events." />
        <div className="availability-data">{availability?.available_data_sources.map((source) => <span key={source}><ShieldCheck size={13} />{source}</span>)}</div>
        <div className="availability-grid">{availability?.capabilities.map((capability) => <article className="availability-card" key={capability.capability}><div><h3>{capability.capability}</h3><span className={`availability-status ${capability.status}`}>{capability.status.replaceAll('_', ' ')}</span></div><p>{capability.details}</p></article>)}</div>
      </section>

      <section className="panel operations-recent"><PanelTitle title="Recent completed analyses" subtitle="Metrics are from persisted analysis records." />
        {overview.recent_analyses.length ? overview.recent_analyses.map((analysis) => <button key={analysis.analysis_job_id} onClick={() => setJobFilter(analysis.analysis_job_id)}><span><strong>{analysis.video_filename}</strong><small>{analysis.analysis_job_id}</small></span><span>{number(analysis.detections_count)} detections · {number(analysis.unique_tracked_entities)} tracked entities</span><time>{dateLabel(analysis.created_at)}</time></button>) : <Empty text="No completed analysis jobs match this filter." />}
      </section>
      <footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL OPERATIONAL INTELLIGENCE</span><span>RECOMMENDATIONS REQUIRE HUMAN REVIEW</span></footer>
    </>}
  </div>
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: number; note: string }) {
  return <article className="panel operations-metric"><div><span>{icon}</span><small>{label}</small></div><strong>{number(value)}</strong><p>{note}</p></article>
}

function PanelTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return <div className="operations-panel-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div>
}

function Empty({ text }: { text: string }) {
  return <div className="operations-empty"><Activity size={16} /><span>{text}</span></div>
}

function Pagination({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  if (total <= limit) return null
  return <div className="operations-pagination"><span>{offset + 1}–{Math.min(offset + limit, total)} of {number(total)}</span><button disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}>Previous</button><button disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>Next</button></div>
}
