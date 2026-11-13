import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Activity, ArrowDownRight, ArrowUpRight, Bot, ChevronDown, CircleHelp,
  Command, Cpu, Database, FileClock, Gauge, HardDrive, LayoutDashboard, Menu,
  Settings2, ShieldCheck, Sparkles, Video, Wifi, X,
} from 'lucide-react'
import { getHealth } from './lib/api'
import VideoIntelligencePage from './features/video-intelligence/VideoIntelligencePage'
import AIReasoningPage from './features/ai-reasoning/AIReasoningPage'
import VisionAgentsPage from './features/vision-agents/VisionAgentsPage'
import OperationalInsightsPage from './features/operational-insights/OperationalInsightsPage'
import ActivityLogsPage from './features/activity-logs/ActivityLogsPage'

const navigation = [
  { label: 'AAI Overview', icon: LayoutDashboard, eyebrow: 'Workspace' },
  { label: 'Video Intelligence', icon: Video, eyebrow: 'Workspace' },
  { label: 'Vision Agents', icon: Bot, eyebrow: 'Intelligence' },
  { label: 'AI Reasoning', icon: Sparkles, eyebrow: 'Intelligence' },
  { label: 'Operational Insights', icon: Gauge, eyebrow: 'Intelligence' },
  { label: 'AI Activity Logs', icon: FileClock, eyebrow: 'System' },
  { label: 'Settings', icon: Settings2, eyebrow: 'System' },
]

type Connection = 'checking' | 'connected' | 'offline'

function App() {
  const [activePage, setActivePage] = useState('AAI Overview')
  const [connection, setConnection] = useState<Connection>('checking')
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    const check = async () => {
      try {
        await getHealth(controller.signal)
        setConnection('connected')
      } catch {
        if (!controller.signal.aborted) setConnection('offline')
      }
    }
    void check()
    const timer = window.setInterval(() => void check(), 15_000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [])

  const choosePage = (label: string) => {
    setActivePage(label)
    setMenuOpen(false)
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark"><span /><span /><span /></div>
          <div><div className="brand-name">VANGUARD<span> AI</span></div><div className="brand-caption">INTELLIGENCE WORKSPACE</div></div>
          <button className="icon-button close-menu" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><X size={17} /></button>
        </div>

        <div className="workspace-switcher">
          <div className="workspace-avatar">WH</div>
          <div className="workspace-copy"><span className="eyebrow">WORKSPACE</span><strong>Warehouse Ops</strong></div>
          <ChevronDown size={15} className="muted-icon" />
        </div>

        {(['Workspace', 'Intelligence', 'System'] as const).map((group) => (
          <div className="nav-group" key={group}>
            <div className="nav-heading">{group}</div>
            {navigation.filter((item) => item.eyebrow === group).map(({ label, icon: Icon }) => (
              <button key={label} className={`nav-item ${activePage === label ? 'active' : ''}`} onClick={() => choosePage(label)}>
                <Icon size={17} strokeWidth={1.8} /><span>{label}</span>
                {label === 'AI Activity Logs' && <span className="nav-dot" />}
              </button>
            ))}
          </div>
        ))}

        <div className="sidebar-bottom">
          <div className="local-card">
            <div className="local-card-icon"><ShieldCheck size={16} /></div>
            <div><strong>Local by design</strong><p>Your workspace runs on this machine.</p></div>
          </div>
          <button className="profile-row"><div className="profile-avatar">V</div><div className="profile-copy"><strong>Local workspace</strong><span>Administrator</span></div><MoreDots /></button>
        </div>
      </aside>

      {menuOpen && <button className="mobile-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={19} /></button>
          <div className="breadcrumbs"><span>Vanguard AI</span><span className="crumb-separator">/</span><strong>{activePage}</strong></div>
          <div className="topbar-actions">
            <div className={`connection-pill ${connection}`}><span className="connection-led" />{connection === 'connected' ? 'API connected' : connection === 'checking' ? 'Connecting' : 'API offline'}</div>
            <button className="icon-button help-button" aria-label="Help"><CircleHelp size={17} /></button>
            <div className="top-avatar">V</div>
          </div>
        </header>

        <div className="page-wrap">
          <AnimatePresence mode="wait">
            <motion.div key={activePage} initial={{ opacity: 0, y: 7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
              {activePage === 'AAI Overview' ? <Overview connection={connection} onNavigate={choosePage} /> : activePage === 'Video Intelligence' ? <VideoIntelligencePage /> : activePage === 'AI Reasoning' ? <AIReasoningPage /> : activePage === 'Vision Agents' ? <VisionAgentsPage /> : activePage === 'Operational Insights' ? <OperationalInsightsPage /> : activePage === 'AI Activity Logs' ? <ActivityLogsPage onNavigate={choosePage} /> : <SectionPage title={activePage} onNavigate={choosePage} />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  )
}

function Overview({ connection, onNavigate }: { connection: Connection; onNavigate: (page: string) => void }) {
  return <>
    <div className="page-heading-row">
      <div><div className="overline"><span className="overline-line" />COMMAND CENTER</div><h1>AAI Overview</h1><p className="page-subtitle">Your operational intelligence, at a glance.</p></div>
      <button className="date-button"><Command size={14} /> LOCAL WORKSPACE <ChevronDown size={14} /></button>
    </div>

    <section className="welcome-banner">
      <div className="welcome-grid" />
      <div className="welcome-content"><div className="welcome-tag"><span className="tag-spark">✳</span> AGENTIC AI INTELLIGENCE</div><h2>See clearly.<br /><span>Act with confidence.</span></h2><p>Bring your operational video and intelligence workflows into one focused workspace.</p><button className="primary-button" onClick={() => onNavigate('Video Intelligence')}>Explore video intelligence <ArrowUpRight size={16} /></button></div>
      <div className="welcome-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit orbit-three" /><div className="art-core"><div className="brand-mark big"><span /><span /><span /></div></div><div className="art-node node-a"><Video size={17} /></div><div className="art-node node-b"><Cpu size={17} /></div><div className="art-node node-c"><Activity size={17} /></div><div className="art-crosshair">+</div></div>
      <div className="banner-index">01 <span>/ 04</span></div>
    </section>

    <div className="section-title-row"><div><h2>System overview</h2><p>Live status from your local workspace.</p></div><span className="live-label"><span /> LIVE CONNECTION</span></div>
    <section className="overview-grid">
      <article className="metric-card api-card"><div className="metric-top"><span className="metric-icon"><Wifi size={17} /></span><span className="metric-label">BACKEND API</span><span className="metric-menu">•••</span></div><div className="metric-value status-value">{connection === 'connected' ? 'Connected' : connection === 'checking' ? 'Checking' : 'Offline'}</div><div className="metric-foot"><span className={`status-inline ${connection}`}><span />{connection === 'connected' ? 'Health check responding' : connection === 'checking' ? 'Checking local service' : 'Start the API to connect'}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span className="metric-icon muted"><Video size={17} /></span><span className="metric-label">VIDEO ANALYSES</span><span className="metric-menu">•••</span></div><div className="metric-value">—</div><div className="metric-foot"><span className="metric-note">No analysis data yet</span></div></article>
      <article className="metric-card"><div className="metric-top"><span className="metric-icon muted"><Bot size={17} /></span><span className="metric-label">ACTIVE AGENTS</span><span className="metric-menu">•••</span></div><div className="metric-value">—</div><div className="metric-foot"><span className="metric-note">AI components not configured</span></div></article>
      <article className="metric-card"><div className="metric-top"><span className="metric-icon muted"><Database size={17} /></span><span className="metric-label">LOCAL STORAGE</span><span className="metric-menu">•••</span></div><div className="metric-value compact">SQLite <span className="not-ready">Not configured</span></div><div className="metric-foot"><span className="metric-note">Storage layer not initialized</span><span className="storage-glyph"><HardDrive size={17} /></span></div></article>
    </section>

    <div className="lower-grid">
      <section className="panel activity-panel"><div className="panel-header"><div><h2>Recent activity</h2><p>Events from your intelligence workspace.</p></div><button className="text-button" onClick={() => onNavigate('AI Activity Logs')}>View activity <ArrowUpRight size={14} /></button></div><div className="empty-state"><div className="empty-icon"><Activity size={19} /></div><strong>No activity yet</strong><p>System events and analysis updates will appear here.</p></div></section>
      <section className="panel readiness-panel"><div className="panel-header"><div><h2>Workspace readiness</h2><p>Foundation services</p></div><span className="readiness-count">01 <span>/ 04</span></span></div><ReadinessRow icon={<Wifi size={15} />} title="API service" detail={connection === 'connected' ? 'Responding locally' : connection === 'checking' ? 'Checking connection' : 'Waiting for local API'} state={connection === 'connected' ? 'ready' : connection === 'checking' ? 'pending' : 'offline'} />
        <ReadinessRow icon={<Database size={15} />} title="Database" detail="Not configured yet" state="pending" />
        <ReadinessRow icon={<Cpu size={15} />} title="Vision engine" detail="Not configured yet" state="pending" />
        <ReadinessRow icon={<Sparkles size={15} />} title="Reasoning model" detail="Not configured yet" state="pending" />
      </section>
    </div>
    <footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL INTELLIGENCE WORKSPACE</span><span>SEE. THINK. PREDICT. ACT.</span></footer>
  </>
}

function ReadinessRow({ icon, title, detail, state }: { icon: React.ReactNode; title: string; detail: string; state: 'ready' | 'pending' | 'offline' }) {
  return <div className="readiness-row"><div className={`readiness-icon ${state}`}>{icon}</div><div className="readiness-copy"><strong>{title}</strong><span>{detail}</span></div><span className={`readiness-state ${state}`}>{state === 'ready' ? 'Ready' : state === 'offline' ? 'Offline' : 'Pending'}</span></div>
}

function SectionPage({ title, onNavigate }: { title: string; onNavigate: (page: string) => void }) {
  const sectionContent: Record<string, { icon: typeof Video; description: string; note: string; action?: string; target?: string }> = {
    'Video Intelligence': { icon: Video, description: 'A focused workspace for reviewing operational footage and analysis results.', note: 'Video upload and analysis are not available yet.', action: 'Back to overview', target: 'AAI Overview' },
    'Vision Agents': { icon: Bot, description: 'Inspect the vision and tracking components that will power video understanding.', note: 'No AI agents are configured.' },
    'AI Reasoning': { icon: Sparkles, description: 'Evidence-led reasoning over structured events from your operations.', note: 'No reasoning model is configured.' },
    'Operational Insights': { icon: Gauge, description: 'Operational findings and recommendations grounded in analyzed activity.', note: 'Insights will appear when analysis data is available.' },
    'AI Activity Logs': { icon: FileClock, description: 'A traceable record of system and intelligence workflow activity.', note: 'There are no activity events yet.' },
    Settings: { icon: Settings2, description: 'Configure local services and workspace preferences.', note: 'Configuration options will be added as services are introduced.' },
  }
  const content = sectionContent[title] ?? sectionContent['Video Intelligence']
  const Icon = content.icon
  return <div className="section-page"><div className="overline"><span className="overline-line" />VANGUARD WORKSPACE</div><h1>{title}</h1><p className="page-subtitle">{content.description}</p><section className="panel section-empty"><div className="section-icon"><Icon size={21} /></div><div className="empty-rule" /><h2>{content.note}</h2><p>This foundation includes the workspace interface and local API connection. No AI processing is running.</p>{content.action && <button className="secondary-button" onClick={() => onNavigate(content.target ?? 'AAI Overview')}>{content.action}<ArrowDownRight size={15} /></button>}</section><footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL INTELLIGENCE WORKSPACE</span><span>SEE. THINK. PREDICT. ACT.</span></footer></div>
}

function MoreDots() { return <span className="more-dots" aria-hidden="true">•••</span> }

export default App
