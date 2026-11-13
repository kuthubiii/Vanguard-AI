import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Bot, CheckCircle2, Circle, Clock3, LoaderCircle, Play, RefreshCw, ShieldAlert, ToggleLeft, ToggleRight } from 'lucide-react'
import './VisionAgentsPage.css'
import { cancelAgentWorkflow, type AgentInfo, type AgentWorkflow, type AnalysisJob, getAgents, getAnalysisJobs, getAgentWorkflow, getAgentWorkflows, setAgentEnabled, startAgentWorkflow } from '../../lib/api'

const terminal = (status: AgentWorkflow['status']) => ['completed', 'failed', 'cancelled'].includes(status)
const time = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
const duration = (value: number | null) => value == null ? '—' : `${(value / 1000).toFixed(1)}s`

export default function VisionAgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [jobs, setJobs] = useState<AnalysisJob[]>([])
  const [jobId, setJobId] = useState('')
  const [workflow, setWorkflow] = useState<AgentWorkflow | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')

  const refresh = async () => {
    const [agentRows, jobRows, workflows] = await Promise.all([getAgents(), getAnalysisJobs(), getAgentWorkflows()])
    setAgents(agentRows)
    const completed = jobRows.items.filter((job) => job.status === 'completed')
    setJobs(completed)
    if (!jobId && completed.length) setJobId(completed[0].job_id)
    if (!workflow && workflows.length) setWorkflow(workflows[0])
  }

  useEffect(() => {
    let active = true
    Promise.all([getAgents(), getAnalysisJobs(), getAgentWorkflows()]).then(([agentRows, jobRows, workflows]) => {
      if (!active) return
      setAgents(agentRows)
      const completed = jobRows.items.filter((job) => job.status === 'completed')
      setJobs(completed)
      if (completed.length) setJobId(completed[0].job_id)
      if (workflows.length) setWorkflow(workflows[0])
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Agent workspace could not be loaded.')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!workflow || terminal(workflow.status)) return
    let active = true
    const timer = window.setInterval(() => {
      void getAgentWorkflow(workflow.workflow_id).then((next) => {
        if (!active) return
        setWorkflow(next)
        if (terminal(next.status)) void refresh().catch(() => undefined)
      }).catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Workflow status could not be refreshed.')
      })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [workflow?.workflow_id, workflow?.status])

  const running = workflow != null && !terminal(workflow.status)
  const selectedJob = jobs.find((job) => job.job_id === jobId)
  const workflowResult = useMemo(() => workflow?.result ?? null, [workflow])

  const start = async () => {
    if (!jobId || starting || running) return
    setStarting(true); setError('')
    try { setWorkflow(await startAgentWorkflow(jobId)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Workflow could not be started.') }
    finally { setStarting(false) }
  }

  const toggle = async (agent: AgentInfo) => {
    setError('')
    try {
      const updated = await setAgentEnabled(agent.agent_id, !agent.enabled)
      setAgents((current) => current.map((item) => item.agent_id === updated.agent_id ? updated : item))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Agent configuration could not be saved.') }
  }

  const cancel = async () => {
    if (!workflow || workflow.current_agent === 'reasoning') return
    setError('')
    try { setWorkflow(await cancelAgentWorkflow(workflow.workflow_id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Workflow cancellation was rejected.') }
  }

  return <div className="vision-agents-page">
    <div className="page-heading-row">
      <div><div className="overline"><span className="overline-line" />INTELLIGENCE ORCHESTRATION</div><h1>Vision Agents</h1><p className="page-subtitle">Coordinate evidence-grounded analysis agents over persisted video results.</p></div>
      <span className="agent-local-tag"><span /> LOCAL WORKFLOW</span>
    </div>
    {error && <div className="agents-alert" role="alert"><AlertCircle size={17} />{error}</div>}
    <section className="panel agents-panel">
      <div className="panel-header"><div><h2>Agent registry</h2><p>Execution status and capabilities from the local API.</p></div><button className="secondary-button" onClick={() => void refresh().catch((e: unknown) => setError(e instanceof Error ? e.message : 'Refresh failed.'))} aria-label="Refresh agents"><RefreshCw size={15} /> Refresh</button></div>
      {loading ? <div className="agents-loading"><LoaderCircle className="spin" size={19} /> Loading agent registry…</div> : <div className="agent-grid">{agents.map((agent) => (
        <article className="agent-card" key={agent.agent_id}>
          <div className="agent-card-head"><span className="agent-icon"><Bot size={18} /></span><span className={`agent-state ${agent.current_status}`}>{agent.current_status}</span></div>
          <h3>{agent.name}</h3><p className="agent-description">{agent.description}</p>
          <div className="agent-capabilities">{agent.capabilities.map((capability) => <span key={capability}>{capability.replaceAll('_', ' ')}</span>)}</div>
          <div className="agent-meta"><span>Version {agent.version}</span><span>Last run {time(agent.last_execution_at)}</span><span>Duration {duration(agent.last_duration_ms)}</span></div>
          {agent.current_task && <div className="agent-task"><Clock3 size={13} />{agent.current_task}</div>}
          {agent.result_summary && <div className="agent-last-result"><strong>Last result</strong><span>{agent.result_summary}</span></div>}
          {agent.error_details && <div className="agent-last-error"><AlertCircle size={14} />{agent.error_details.message ?? agent.error_details.code}</div>}
          <button className={`agent-toggle ${agent.enabled ? 'enabled' : ''}`} onClick={() => void toggle(agent)} disabled={running || agent.current_status === 'running'} aria-pressed={agent.enabled}>
            {agent.enabled ? <ToggleRight size={19} /> : <ToggleLeft size={19} />}{agent.enabled ? 'Enabled' : 'Disabled'}
          </button>
        </article>
      ))}</div>}
    </section>

    <section className="panel workflow-panel">
      <div className="panel-header"><div><h2>Workflow monitor</h2><p>Runs agents sequentially against an existing completed analysis.</p></div><div className="workflow-actions"><span className={`workflow-state ${workflow?.status ?? 'idle'}`}>{workflow?.status ?? 'idle'}</span>{running && workflow?.current_agent !== 'reasoning' && <button className="secondary-button" onClick={() => void cancel()}>Cancel safely</button>}</div></div>
      <div className="workflow-launch">
        <label htmlFor="workflow-job">Completed analysis</label>
        <select id="workflow-job" value={jobId} onChange={(event) => setJobId(event.target.value)} disabled={running || starting}>
          {jobs.length === 0 && <option value="">No completed analyses available</option>}
          {jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.job_id} · {job.detections_count} detections</option>)}
        </select>
        <button className="primary-button" onClick={() => void start()} disabled={!jobId || starting || running || !agents.some((agent) => agent.enabled && ['vision', 'tracking'].includes(agent.agent_id))}>
          {starting || running ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{running ? 'Workflow running' : 'Start workflow'}
        </button>
      </div>
      {selectedJob && <p className="workflow-job-caption">Selected job: {selectedJob.job_id} · status completed · {selectedJob.detections_count} detections</p>}
      {workflow && <div className="workflow-details">
        <div className="workflow-id-row"><span>Workflow ID</span><code>{workflow.workflow_id}</code><span>Created {time(workflow.created_at)}</span></div>
        <div className="workflow-steps">{workflow.agent_execution_history.map((execution) => <article className={`workflow-step ${execution.status}`} key={execution.agent_id}>
          <div className="step-icon">{execution.status === 'running' ? <LoaderCircle className="spin" size={16} /> : execution.status === 'completed' ? <CheckCircle2 size={16} /> : execution.status === 'failed' ? <AlertCircle size={16} /> : execution.status === 'skipped' ? <ShieldAlert size={16} /> : <Circle size={14} />}</div>
          <div className="step-copy"><strong>{agents.find((agent) => agent.agent_id === execution.agent_id)?.name ?? execution.agent_id}</strong><span>{execution.current_task ?? execution.status}</span>
            {execution.result_summary && <p>{execution.result_summary}</p>}{execution.error_details && <p className="step-error">{execution.error_details.message ?? execution.error_details.code}</p>}
          </div><span className="step-duration">{duration(execution.duration_ms)}</span>
        </article>)}</div>
        {workflow.error_details && <div className="workflow-failure"><AlertCircle size={16} /><div><strong>{workflow.error_details.code ?? 'Workflow failed'}</strong><span>{workflow.error_details.message ?? 'The workflow stopped after an agent error.'}</span></div></div>}
        {workflowResult && <div className="workflow-result"><div><CheckCircle2 size={16} /><strong>Persisted workflow result</strong></div><p>{String((workflowResult.operations_result as { reasoning_summary?: string | null } | undefined)?.reasoning_summary ?? (workflowResult.operations_result as { observations?: string[] } | undefined)?.observations?.join(' ') ?? 'Workflow completed with persisted agent summaries.')}</p>
          <details><summary>View structured result</summary><pre>{JSON.stringify(workflowResult, null, 2)}</pre></details>
        </div>}
      </div>}
      {!workflow && <div className="workflow-empty"><Circle size={17} /> No workflow has been started in this session.</div>}
    </section>
    <footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL INTELLIGENCE WORKSPACE</span><span>AGENT STATUS IS API-REPORTED</span></footer>
  </div>
}
