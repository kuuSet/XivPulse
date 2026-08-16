import {
  AlertTriangle,
  ArrowDownUp,
  Bookmark,
  BookmarkCheck,
  Building2,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileText,
  Inbox,
  KeyRound,
  LayoutDashboard,
  Library,
  Map,
  ListFilter,
  LoaderCircle,
  LogOut,
  Mail,
  Menu,
  MoreHorizontal,
  Network,
  NotebookTabs,
  Plus,
  Radar,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings as SettingsIcon,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Star,
  TrendingUp,
  ThumbsDown,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Wifi,
  X,
} from 'lucide-react'
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './lib/api'
import { sampleDailyReport, sampleDashboard, sampleProfile, sampleProjects, samplePromptCatalog, sampleRoadmap, sampleSettings, sampleTrends } from './sample'
import type {
  BackendTopic,
  AnalysisPromptCatalog,
  AuthSession,
  DailyAggregateItem,
  DailyReport,
  DailyReportArchiveItem,
  DashboardData,
  DirectionCatalog,
  DirectionCatalogItem,
  Feedback,
  FeedbackAction,
  FeedbackReason,
  Paper,
  PaperPage,
  PaperAnalysis,
  Profile,
  ProjectDraft,
  ProjectMembership,
  ReadingData,
  ReadingState,
  ResearchProject,
  ResearchRoadmap,
  Settings,
  MethodCandidate,
  MethodCatalog,
  JobInfo,
  TopicTrendItem,
  TopicTrends,
} from './types'

type View = 'today' | 'library' | 'digest' | 'projects' | 'trends' | 'profile' | 'settings'
type LoadState = 'idle' | 'loading' | 'ready' | 'error'
type Toast = { id: number; kind: 'success' | 'error'; message: string }

const NAV_ITEMS: Array<{ id: View; label: string; icon: typeof Radar }> = [
  { id: 'today', label: '今日论文', icon: LayoutDashboard },
  { id: 'library', label: '论文库', icon: Library },
  { id: 'digest', label: '研究日报', icon: Mail },
  { id: 'projects', label: '分类库', icon: NotebookTabs },
  { id: 'trends', label: '研究方向', icon: TrendingUp },
  { id: 'profile', label: '关注设置', icon: SlidersHorizontal },
  { id: 'settings', label: '系统设置', icon: SettingsIcon },
]

const READING_STATE_OPTIONS: Array<[ReadingState, string]> = [
  ['unread', '未读'], ['queued', '待读队列'], ['reading', '阅读中'], ['read', '已读'], ['reproduce', '准备复现'], ['cited', '已引用'], ['archived', '已归档'],
]

const PRIORITY_LABELS: Record<number, string> = { 1: '低', 2: '稍后', 3: '普通', 4: '优先', 5: '最高' }

const FEEDBACK_REASON_OPTIONS: Array<[FeedbackReason, string]> = [
  ['not_relevant', '主题不相关'], ['incremental', '方法增量有限'], ['weak_evidence', '证据不足'], ['no_real_robot', '缺少真实机器人'],
  ['no_code', '未开放代码'], ['duplicate', '内容重复'], ['poor_writing', '表达不清'], ['topic_pause', '暂缓关注此主题'],
]

const isDemo = new URLSearchParams(window.location.search).get('demo') === '1'
const BRAND_LOGO = '/xivpulse-logo.png'
const requestedView = new URLSearchParams(window.location.search).get('view') as View | null
const requestedPaper = new URLSearchParams(window.location.search).get('paper')
const initialView: View = NAV_ITEMS.some((item) => item.id === requestedView) ? requestedView! : 'today'

function App() {
  const [view, setView] = useState<View>(initialView)
  const [authSession, setAuthSession] = useState<AuthSession | null>(isDemo ? { enabled: false, authenticated: true } : null)
  const [authError, setAuthError] = useState('')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [dashboardState, setDashboardState] = useState<LoadState>('loading')
  const [dashboardError, setDashboardError] = useState('')
  const [selectedId, setSelectedId] = useState<Paper['id'] | null>(requestedPaper)
  const [refreshing, setRefreshing] = useState(false)
  const [job, setJob] = useState<JobInfo | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [projects, setProjects] = useState<ResearchProject[]>([])

  const pushToast = useCallback((kind: Toast['kind'], message: string) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, kind, message }])
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 3200)
  }, [])

  const loadDashboard = useCallback(async () => {
    setDashboardState('loading')
    setDashboardError('')
    try {
      const data = isDemo ? sampleDashboard : await api.dashboard()
      setDashboard(data)
      setSelectedId((current) => current ?? data.items[0]?.id ?? null)
      setDashboardState('ready')
    } catch (error) {
      setDashboard(null)
      setDashboardState('error')
      setDashboardError(error instanceof Error ? error.message : '无法连接后端服务')
    }
  }, [])

  const updatePaper = useCallback((id: Paper['id'], update: (paper: Paper) => Paper) => {
    setDashboard((current) =>
      current ? { ...current, items: current.items.map((paper) => (paper.id === id ? update(paper) : paper)) } : current,
    )
  }, [])

  const syncProjects = useCallback((next: ResearchProject[]) => {
    setProjects(next)
    const memberships = next.flatMap((project) => (project.papers ?? []).map((membership) => ({
      ...membership,
      project_id: project.id,
      project_name: project.name,
      project_color: project.color,
    })))
    setDashboard((current) => current ? {
      ...current,
      items: current.items.map((paper) => ({ ...paper, project_memberships: memberships.filter((item) => String(item.paper_id) === String(paper.id)) })),
    } : current)
  }, [])

  const loadProjects = useCallback(async () => {
    try {
      if (isDemo) {
        syncProjects(sampleProjects)
        return
      }
      const listed = await api.projects()
      const detailed = await Promise.all(listed.map(async (project) => {
        try { return await api.project(project.id) } catch { return project }
      }))
      syncProjects(detailed)
    } catch {
      // Project errors are rendered in the project view; the paper workflow remains usable.
    }
  }, [syncProjects])

  useEffect(() => {
    if (isDemo) return
    void api.authSession()
      .then((session) => { setAuthSession(session); setAuthError('') })
      .catch((error) => { setAuthError(error instanceof Error ? error.message : '无法连接服务'); setAuthSession({ enabled: false, authenticated: false }) })
  }, [])

  useEffect(() => {
    if (!authSession?.authenticated) return
    void loadDashboard()
    void loadProjects()
  }, [authSession?.authenticated, loadDashboard, loadProjects])

  useEffect(() => {
    const restoreLocation = () => {
      const params = new URLSearchParams(window.location.search)
      const nextView = params.get('view') as View | null
      setView(NAV_ITEMS.some((item) => item.id === nextView) ? nextView! : 'today')
      setSelectedId(params.get('paper'))
    }
    window.addEventListener('popstate', restoreLocation)
    return () => window.removeEventListener('popstate', restoreLocation)
  }, [])

  useEffect(() => {
    if (isDemo || !authSession?.authenticated) return
    let cancelled = false
    let timer: number | undefined
    let lastCompletedId = ''
    let wasBusy = false
    const poll = async () => {
      try {
        const status = await api.status()
        if (cancelled) return
        setJob(status.active_job ?? status.latest_job ?? null)
        setRefreshing(status.busy)
        if (
          !status.busy
          && status.latest_job?.status === 'completed'
          && status.latest_job.id !== lastCompletedId
        ) {
          lastCompletedId = status.latest_job.id
          if (wasBusy) void loadDashboard()
        }
        wasBusy = status.busy
      } catch {
        // The dashboard already owns the full service-error state.
      }
      if (!cancelled) timer = window.setTimeout(poll, 3000)
    }
    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [authSession?.authenticated, loadDashboard])

  const handleReading = useCallback(async (paper: Paper, reading: ReadingData) => {
    const previous = getReading(paper)
    updatePaper(paper.id, (item) => applyReading(item, reading))
    if (isDemo) {
      pushToast('success', '阅读进度已保存')
      return true
    }
    try {
      const saved = await api.saveReading(paper.id, reading)
      updatePaper(paper.id, (item) => applyReading(item, { ...reading, ...saved }))
      pushToast('success', '阅读进度已保存')
      return true
    } catch (error) {
      updatePaper(paper.id, (item) => applyReading(item, previous))
      pushToast('error', error instanceof Error ? error.message : '阅读进度保存失败')
      return false
    }
  }, [pushToast, updatePaper])

  const handleDetailedFeedback = useCallback(async (paper: Paper, feedback: Feedback) => {
    const previous = paper.feedback ?? null
    updatePaper(paper.id, (item) => ({ ...item, feedback, feedback_reasons: feedback.reasons ?? [] }))
    if (isDemo) {
      pushToast('success', '推荐反馈已保存')
      return true
    }
    try {
      const action = feedback.action === 'skip' || feedback.action === 'dismiss' ? 'dismiss' : feedback.action ?? 'neutral'
      await api.feedback(paper.id, { action, rating: feedback.rating, note: feedback.note ?? '', reasons: feedback.reasons ?? [] })
      if (action === 'save' || previous?.action === 'save') await loadProjects()
      pushToast('success', '推荐反馈已保存')
      return true
    } catch (error) {
      updatePaper(paper.id, (item) => ({ ...item, feedback: previous, feedback_reasons: previous?.reasons ?? [] }))
      pushToast('error', error instanceof Error ? error.message : '推荐反馈保存失败')
      return false
    }
  }, [loadProjects, pushToast, updatePaper])

  const handleFeedback = useCallback(
    async (paper: Paper, action: FeedbackAction) => {
      const previous = paper.feedback?.action === 'skip' ? 'dismiss' : (paper.feedback?.action ?? null)
      const next = previous === action ? null : action
      updatePaper(paper.id, (item) => ({ ...item, feedback: { ...item.feedback, action: next } }))
      if (isDemo) {
        pushToast('success', next === 'save' ? '已收藏论文' : next === 'dismiss' ? '已减少此类推荐' : '反馈已记录')
        return true
      }
      try {
        await api.feedback(paper.id, next ?? 'neutral')
        if (next === 'save' || previous === 'save') await loadProjects()
        pushToast('success', next === 'save' ? '已收藏论文' : next === 'dismiss' ? '已减少此类推荐' : '反馈已记录')
        return true
      } catch (error) {
        updatePaper(paper.id, (item) => ({ ...item, feedback: { ...item.feedback, action: previous } }))
        pushToast('error', error instanceof Error ? error.message : '反馈保存失败')
        return false
      }
    },
    [loadProjects, pushToast, updatePaper],
  )

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      if (!isDemo) {
        const result = await api.refresh()
        setJob({ id: result.job_id, job_id: result.job_id, kind: 'daily', status: result.status, progress: 0, total: 0, message: result.message, created_at: new Date().toISOString() })
        setRefreshing(true)
        pushToast('success', result.message || '抓取任务已启动')
      } else {
        await new Promise((resolve) => window.setTimeout(resolve, 650))
        pushToast('success', '演示数据已刷新')
      }
    } catch (error) {
      pushToast('error', error instanceof Error ? error.message : '刷新失败')
    } finally {
      if (isDemo) setRefreshing(false)
    }
  }

  const navigate = (next: View) => {
    setView(next)
    const url = new URL(window.location.href)
    url.searchParams.set('view', next)
    url.searchParams.delete('paper')
    window.history.replaceState(null, '', url)
    setMobileNavOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const openPaper = useCallback((id: Paper['id'], targetView: View = view) => {
    setView(targetView)
    setSelectedId(id)
    const url = new URL(window.location.href)
    url.searchParams.set('view', targetView)
    url.searchParams.set('paper', String(id))
    window.history.pushState(null, '', url)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [view])

  const closePaper = useCallback(() => {
    const url = new URL(window.location.href)
    url.searchParams.delete('paper')
    window.history.pushState(null, '', url)
    setSelectedId(null)
  }, [])

  const title = NAV_ITEMS.find((item) => item.id === view)?.label ?? '研究雷达'

  if (authSession === null) return <div className="auth-loading"><LoaderCircle className="spin" size={24} /><span>正在连接研究服务</span></div>
  if (!authSession.authenticated) return <LoginPage session={authSession} error={authError} onAuthenticated={setAuthSession} />

  return (
    <div className="app-shell">
      <Sidebar current={view} open={mobileNavOpen} authEnabled={authSession.enabled} onNavigate={navigate} onClose={() => setMobileNavOpen(false)} onLogout={() => { void api.logout().finally(() => setAuthSession({ enabled: true, authenticated: false })) }} />

      <main className="main-shell">
        <header className="mobile-header">
          <button className="icon-button" onClick={() => setMobileNavOpen(true)} aria-label="打开导航">
            <Menu size={20} />
          </button>
          <div className="mobile-brand"><img src={BRAND_LOGO} alt="XivPulse" /><span>{title}</span></div>
          <span className={`status-dot ${dashboardState === 'error' ? 'offline' : ''}`} aria-label="服务状态" />
        </header>

        {(view === 'today' || view === 'library') && (
          <PaperWorkspace
            mode={view}
            data={dashboard}
            state={dashboardState}
            error={dashboardError}
            selectedId={selectedId}
            refreshing={refreshing}
            job={job ?? dashboard?.active_job ?? null}
            onRetry={loadDashboard}
            onRefresh={handleRefresh}
            onSelect={(id) => openPaper(id, view)}
            onCloseDetail={closePaper}
            onFeedback={handleFeedback}
            onSaveReading={handleReading}
            onSaveDetailedFeedback={handleDetailedFeedback}
            projects={projects}
            onProjectsChange={syncProjects}
            onToast={pushToast}
          />
        )}
        {view === 'digest' && (
          <DigestView data={dashboard} state={dashboardState} error={dashboardError} onRetry={loadDashboard} onToast={pushToast} />
        )}
        {view === 'projects' && <ProjectsView projects={projects} onProjectsChange={syncProjects} onPapersReload={loadDashboard} onOpenPaper={(id) => openPaper(id, 'library')} onToast={pushToast} />}
        {view === 'trends' && <TrendsView onToast={pushToast} />}
        {view === 'profile' && <ProfileView onToast={pushToast} />}
        {view === 'settings' && <SettingsView onToast={pushToast} />}
      </main>

      <MobileTabBar current={view} onNavigate={navigate} onMore={() => setMobileNavOpen(true)} />
      <div className="toast-stack" aria-live="polite">
        {toasts.map((toast) => (
          <div className={`toast ${toast.kind}`} key={toast.id}>
            {toast.kind === 'success' ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}
            <span>{toast.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function LoginPage({ session, error, onAuthenticated }: { session: AuthSession; error: string; onAuthenticated: (session: AuthSession) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState(error)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true); setMessage('')
    try {
      onAuthenticated(await api.login(username.trim(), password))
    } catch (loginError) {
      setMessage(loginError instanceof Error ? loginError.message : '登录失败')
    } finally { setSubmitting(false) }
  }
  return <main className="login-page"><section className="login-panel"><div className="login-brand"><img src={BRAND_LOGO} alt="XivPulse" /><small>私人研究工作台</small></div><header><h1>登录研究服务</h1><p>使用服务器管理员凭据继续。</p></header>{message && <div className="login-error"><CircleAlert size={16} /><span>{message}</span></div>}<form onSubmit={submit}><label className="field"><span>用户名</span><input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label><label className="field"><span>密码</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><button className="button primary" disabled={submitting || !username.trim() || !password}><KeyRound size={16} />{submitting ? '正在验证' : '登录'}</button></form>{!session.enabled && <small className="login-status">服务连接尚未就绪，请确认服务器地址和证书信任状态。</small>}</section></main>
}

function Sidebar({ current, open, authEnabled, onNavigate, onClose, onLogout }: { current: View; open: boolean; authEnabled: boolean; onNavigate: (view: View) => void; onClose: () => void; onLogout: () => void }) {
  return (
    <>
      <div className={`nav-scrim ${open ? 'visible' : ''}`} onClick={onClose} />
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="brand-block">
          <img className="brand-logo" src={BRAND_LOGO} alt="XivPulse" />
          <button className="icon-button close-nav" onClick={onClose} aria-label="关闭导航"><X size={19} /></button>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button key={id} className={current === id ? 'active' : ''} onClick={() => onNavigate(id)}>
              <Icon size={18} strokeWidth={1.9} />
              <span>{label}</span>
              {id === 'today' && <i className="nav-badge">今日</i>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="service-line"><span className="status-dot" /> 本地服务运行中</div>
          <span>{isDemo ? '演示数据模式' : '数据仅保存在本机'}</span>
          {authEnabled && <button className="sidebar-logout" onClick={onLogout}><LogOut size={14} />退出登录</button>}
        </div>
      </aside>
    </>
  )
}

function MobileTabBar({ current, onNavigate, onMore }: { current: View; onNavigate: (view: View) => void; onMore: () => void }) {
  const coreItems = NAV_ITEMS.filter((item) => ['today', 'library', 'digest'].includes(item.id))
  const moreActive = !coreItems.some((item) => item.id === current)
  return (
    <nav className="mobile-tabs" aria-label="移动端导航">
      {coreItems.map(({ id, label, icon: Icon }) => (
        <button className={current === id ? 'active' : ''} key={id} onClick={() => onNavigate(id)}>
          <Icon size={20} />
          <span>{label}</span>
        </button>
      ))}
      <button className={moreActive ? 'active' : ''} onClick={onMore}><MoreHorizontal size={20} /><span>更多</span></button>
    </nav>
  )
}

interface PaperWorkspaceProps {
  mode: 'today' | 'library'
  data: DashboardData | null
  state: LoadState
  error: string
  selectedId: Paper['id'] | null
  refreshing: boolean
  job: JobInfo | null
  onRetry: () => void
  onRefresh: () => void
  onSelect: (id: Paper['id']) => void
  onCloseDetail: () => void
  onFeedback: (paper: Paper, action: FeedbackAction) => Promise<boolean>
  onSaveReading: (paper: Paper, reading: ReadingData) => Promise<boolean>
  onSaveDetailedFeedback: (paper: Paper, feedback: Feedback) => Promise<boolean>
  projects: ResearchProject[]
  onProjectsChange: (projects: ResearchProject[]) => void
  onToast: (kind: Toast['kind'], message: string) => void
}

function PaperWorkspace(props: PaperWorkspaceProps) {
  const { mode, data, state, error, selectedId, refreshing, job, onRetry, onRefresh, onSelect, onCloseDetail, onFeedback, onSaveReading, onSaveDetailedFeedback, projects, onProjectsChange, onToast } = props
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [priority, setPriority] = useState(mode === 'today' ? 'recommended' : 'all')
  const [sort, setSort] = useState<'recommendation' | 'relevance' | 'time'>('recommendation')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(Boolean(new URLSearchParams(window.location.search).get('paper')))
  const [linkedPaper, setLinkedPaper] = useState<Paper | null>(null)
  const [libraryPage, setLibraryPage] = useState<PaperPage | null>(null)
  const [libraryState, setLibraryState] = useState<LoadState>('idle')
  const [libraryError, setLibraryError] = useState('')
  const [libraryReload, setLibraryReload] = useState(0)
  const [page, setPage] = useState(1)
  const pageSize = 25

  useEffect(() => {
    setPriority(mode === 'today' ? 'recommended' : 'all')
  }, [mode])

  useEffect(() => {
    if (mode === 'library') setPage(1)
  }, [mode, query, category, priority, sort])

  useEffect(() => {
    if (mode !== 'library' || isDemo) return
    let active = true
    const timer = window.setTimeout(() => {
      setLibraryState('loading')
      setLibraryError('')
      void api.papers({
        q: query.trim() || undefined,
        category: category === 'all' ? undefined : category,
        priority: priority === 'recommended' ? 'high' : undefined,
        feedback: priority === 'saved' ? 'save' : undefined,
        reading_state: priority === 'unread' ? 'unread' : undefined,
        sort,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }).then((result) => {
        if (!active) return
        setLibraryPage(result)
        setLibraryState('ready')
      }).catch((loadError) => {
        if (!active) return
        setLibraryError(loadError instanceof Error ? loadError.message : '论文库加载失败')
        setLibraryState('error')
      })
    }, query.trim() ? 250 : 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [category, libraryReload, mode, page, priority, query, sort])

  const categories = useMemo(
    () => mode === 'library'
      ? (isDemo ? Array.from(new Set(sampleDashboard.items.flatMap((paper) => paper.categories))).sort() : libraryPage?.categories ?? [])
      : Array.from(new Set(data?.items.flatMap((paper) => paper.categories) ?? [])).sort(),
    [data, libraryPage, mode],
  )

  const papers = useMemo(() => {
    if (mode === 'library' && !isDemo) return [...(libraryPage?.items ?? [])]
    const normalized = query.trim().toLowerCase()
    const source = mode === 'library' && isDemo ? sampleDashboard.items : data?.items ?? []
    return [...source]
      .filter((paper) => {
        if (category !== 'all' && !paper.categories.includes(category)) return false
        if (priority === 'recommended' && paper.relevance_score < 80) return false
        if (priority === 'saved' && paper.feedback?.action !== 'save') return false
        if (priority === 'unread' && getReading(paper).state !== 'unread') return false
        if (!normalized) return true
        return [paper.title, paper.abstract, paper.authors.join(' '), paper.categories.join(' '), paper.match_reasons.join(' ')]
          .join(' ')
          .toLowerCase()
          .includes(normalized)
      })
      .sort((a, b) => {
        if (sort === 'time') return new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime()
        if (sort === 'relevance') return b.relevance_score - a.relevance_score
        return b.relevance_score - a.relevance_score
      })
  }, [data, libraryPage, mode, query, category, priority, sort])

  const papersWithMemberships = useMemo(() => {
    if (!projects.length) return papers
    const memberships = projects.flatMap((project) => (project.papers ?? []).map((membership) => ({
      ...membership,
      project_id: project.id,
      project_name: project.name,
      project_color: project.color,
    })))
    return papers.map((paper) => {
      const paperMemberships = memberships.filter((item) => String(item.paper_id) === String(paper.id))
      return { ...paper, project_memberships: paperMemberships, projects: paperMemberships }
    })
  }, [papers, projects])

  const selected = papersWithMemberships.find((paper) => String(paper.id) === String(selectedId))
    ?? (linkedPaper && String(linkedPaper.id) === String(selectedId) ? linkedPaper : null)
    ?? (selectedId === null ? papersWithMemberships[0] : null)

  useEffect(() => {
    const linkedId = new URLSearchParams(window.location.search).get('paper')
    setDetailOpen(Boolean(linkedId))
    if (mode !== 'library' || !selectedId || papersWithMemberships.some((paper) => String(paper.id) === String(selectedId))) return
    let active = true
    if (isDemo) {
      setLinkedPaper(sampleDashboard.items.find((paper) => String(paper.id) === String(selectedId)) ?? null)
      return
    }
    void api.paper(selectedId).then((paper) => { if (active) setLinkedPaper(paper) }).catch((loadError) => {
      if (active) onToast('error', loadError instanceof Error ? loadError.message : '论文不存在或无法加载')
    })
    return () => { active = false }
  }, [mode, onToast, papersWithMemberships, selectedId])

  const updateLibraryPaper = useCallback((paperId: Paper['id'], update: (paper: Paper) => Paper) => {
    setLibraryPage((current) => current ? {
      ...current,
      items: current.items.map((paper) => String(paper.id) === String(paperId) ? update(paper) : paper),
    } : current)
  }, [])

  const savePaperFeedback = async (paper: Paper, action: FeedbackAction) => {
    const previous = paper.feedback?.action === 'skip' ? 'dismiss' : (paper.feedback?.action ?? null)
    const next = previous === action ? null : action
    if (mode === 'library') updateLibraryPaper(paper.id, (item) => ({ ...item, feedback: { ...item.feedback, action: next } }))
    const saved = await onFeedback(paper, action)
    if (!saved && mode === 'library') updateLibraryPaper(paper.id, (item) => ({ ...item, feedback: { ...item.feedback, action: previous } }))
  }

  const savePaperReading = async (paper: Paper, reading: ReadingData) => {
    const saved = await onSaveReading(paper, reading)
    if (saved && mode === 'library') updateLibraryPaper(paper.id, (item) => applyReading(item, reading))
    return saved
  }

  const savePaperDetailedFeedback = async (paper: Paper, feedback: Feedback) => {
    const saved = await onSaveDetailedFeedback(paper, feedback)
    if (saved && mode === 'library') updateLibraryPaper(paper.id, (item) => ({ ...item, feedback, feedback_reasons: feedback.reasons ?? [] }))
    return saved
  }

  const selectPaper = (id: Paper['id']) => {
    onSelect(id)
    setDetailOpen(true)
  }

  if (mode === 'library' && libraryState === 'loading' && !libraryPage) return <WorkspaceSkeleton />
  if (mode === 'library' && libraryState === 'error' && !libraryPage) return <PageError message={libraryError} onRetry={() => setLibraryReload((value) => value + 1)} />
  if (mode === 'today' && state === 'loading' && !data) return <WorkspaceSkeleton />
  if (mode === 'today' && state === 'error') return <PageError message={error} onRetry={onRetry} />

  return (
    <div className="workspace-page">
      <section className="page-heading dashboard-heading">
        <div>
          <div className="eyebrow-row">
            {mode === 'today' ? <><CalendarDays size={14} /> {formatFullDate(data?.date)}</> : <><Library size={14} /> 本地论文索引</>}
          </div>
          <h1>{mode === 'today' ? '今日论文' : '论文库'}</h1>
          <p>{mode === 'today' ? '已按你的研究画像筛选并排序今日更新。' : '检索和回看所有已经完成摘要初筛的论文。'}</p>
        </div>
        <button className="button primary" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw size={16} className={refreshing ? 'spin' : ''} />
          {refreshing ? '正在启动' : '抓取最新'}
        </button>
      </section>

      {job && <JobStatusBanner job={job} />}

      {mode === 'today' && data && (
        <section className="metric-band" aria-label="今日统计">
          <Metric label="本次拉取" value={data.processing_summary?.latest_fetched_count ?? data.total_papers} note="最近一次完整日更" />
          <Metric label="摘要初筛" value={data.processing_summary?.latest_screened_count ?? data.screened_papers} note={data.processing_summary?.screening_queue_total ? `候选 ${data.processing_summary.screening_queue_total} · 失败 ${data.processing_summary.screening_failed ?? 0}` : `队列待处理 ${data.processing_summary?.pending_screening_count ?? 0} 篇`} />
          <Metric label="全文深读" value={data.processing_summary?.latest_deep_analyzed_count ?? 0} note={`达标待深读 ${data.processing_summary?.pending_deep_count ?? 0} 篇`} accent />
          <Metric label="重点推荐" value={data.high_priority_papers} note="今日 80 分以上" />
          <div className="update-summary">
            <span>上次更新</span>
            <strong>{data.processing_summary?.latest_update_at ? formatDateTime(data.processing_summary.latest_update_at) : '尚无完整日更'}</strong>
            <small>初筛每批 {data.processing_summary?.screening_limit ?? 30} · 深读最多 {data.processing_summary?.deep_analysis_limit ?? 5} · 门槛 {Math.round(data.processing_summary?.deep_relevance_threshold ?? 90)}</small>
          </div>
        </section>
      )}

      <section className="paper-workbench">
        <div className={`paper-column ${mode === 'library' ? 'paginated' : ''}`}>
          <div className="paper-tools">
            <label className="search-box">
              <Search size={17} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、作者或关键词" aria-label="搜索论文" />
              {query && <button onClick={() => setQuery('')} aria-label="清除搜索"><X size={15} /></button>}
            </label>
            <button className={`icon-button filter-toggle ${filtersOpen ? 'active' : ''}`} onClick={() => setFiltersOpen((value) => !value)} aria-label="筛选">
              <ListFilter size={18} />
            </button>
          </div>

          <div className={`filter-row ${filtersOpen ? 'mobile-open' : ''}`}>
            <SelectControl value={priority} onChange={setPriority} label="论文范围" icon={<ListFilter size={14} />} options={[
              ['recommended', '重点推荐'], ['all', '全部论文'], ['saved', '已收藏'], ['unread', '未读'],
            ]} />
            <SelectControl value={category} onChange={setCategory} label="分类" options={[
              ['all', '全部分类'], ...categories.map((item) => [item, item] as [string, string]),
            ]} />
            <SelectControl value={sort} onChange={(value) => setSort(value as typeof sort)} label="排序" icon={<ArrowDownUp size={14} />} options={[
              ['recommendation', '推荐优先'], ['relevance', '相关度优先'], ['time', '最新提交'],
            ]} />
          </div>

          <div className="result-summary">
            <span>{mode === 'library' && !isDemo ? `共 ${libraryPage?.total ?? 0} 篇已初筛论文` : `${papersWithMemberships.length} 篇论文`}</span>
            {(query || category !== 'all' || priority !== (mode === 'today' ? 'recommended' : 'all')) && (
              <button onClick={() => { setQuery(''); setCategory('all'); setPriority(mode === 'today' ? 'recommended' : 'all') }}>
                <RotateCcw size={13} /> 重置
              </button>
            )}
          </div>

          <div className="paper-list">
            {papersWithMemberships.length ? papersWithMemberships.map((paper) => (
              <PaperListItem
                key={paper.id}
                paper={paper}
                active={selected?.id === paper.id}
                onSelect={() => selectPaper(paper.id)}
                onFeedback={(action) => { void savePaperFeedback(paper, action) }}
              />
            )) : <EmptyPapers filtered={Boolean(query || category !== 'all' || priority !== 'all')} />}
          </div>
          {mode === 'library' && !isDemo && libraryPage && libraryPage.pages > 1 && <nav className="paper-pagination" aria-label="论文库分页">
            <button className="icon-button" disabled={!libraryPage.has_previous || libraryState === 'loading'} onClick={() => setPage((value) => Math.max(1, value - 1))} title="上一页"><ChevronLeft size={16} /></button>
            <span>第 {libraryPage.page} / {libraryPage.pages} 页</span>
            <button className="icon-button" disabled={!libraryPage.has_next || libraryState === 'loading'} onClick={() => setPage((value) => value + 1)} title="下一页"><ChevronRight size={16} /></button>
          </nav>}
        </div>

        <div className={`detail-column ${detailOpen ? 'mobile-visible' : ''}`}>
          {selected ? (
            <PaperDetail paper={selected} projects={projects} onProjectsChange={onProjectsChange} onToast={onToast} onClose={() => { setDetailOpen(false); onCloseDetail() }} onFeedback={(action) => { void savePaperFeedback(selected, action) }} onSaveReading={(reading) => savePaperReading(selected, reading)} onSaveDetailedFeedback={(feedback) => savePaperDetailedFeedback(selected, feedback)} />
          ) : (
            <div className="detail-placeholder"><FileText size={28} /><p>从左侧选择一篇论文查看分析</p></div>
          )}
        </div>
      </section>
    </div>
  )
}

function JobStatusBanner({ job }: { job: JobInfo }) {
  const running = job.status === 'queued' || job.status === 'running'
  const failed = job.status === 'failed'
  const result = job.result ?? {}
  const summary = job.status === 'completed'
    ? `抓取 ${Number(result.fetched ?? 0)} 篇 · 初筛 ${Number(result.screened ?? 0)}/${Number(result.screening_queue_total ?? result.screened ?? 0)} 篇 · 失败 ${Number(result.screening_failed ?? 0)} 篇 · 深读 ${Number(result.analyzed ?? 0)} 篇`
    : failed ? (job.error || job.message || '任务失败') : (job.message || '正在执行每日流程')
  return <section className={`job-status-banner ${failed ? 'failed' : running ? 'running' : 'completed'}`} aria-live="polite"><div>{running ? <LoaderCircle className="spin" size={17} /> : failed ? <CircleAlert size={17} /> : <CheckCircle2 size={17} />}<span><strong>{running ? '每日任务进行中' : failed ? '最近一次任务失败' : '最近一次任务已完成'}</strong><small>{summary}</small></span></div><div className="job-progress"><span style={{ width: `${Math.max(running ? 4 : 0, Math.min(100, job.progress ?? 0))}%` }} /></div></section>
}

function Metric({ label, value, note, accent = false }: { label: string; value: number; note: string; accent?: boolean }) {
  return (
    <div className={`metric ${accent ? 'accent' : ''}`}>
      <div><span>{label}</span><strong>{value}</strong></div>
      <small>{note}</small>
    </div>
  )
}

function SelectControl({ value, onChange, options, label, icon }: { value: string; onChange: (value: string) => void; options: [string, string][]; label: string; icon?: ReactNode }) {
  return (
    <label className="select-control">
      {icon}
      <span className="sr-only">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel]) => <option value={optionValue} key={optionValue}>{optionLabel}</option>)}
      </select>
      <ChevronDown size={14} />
    </label>
  )
}

function PaperListItem({ paper, active, onSelect, onFeedback }: { paper: Paper; active: boolean; onSelect: () => void; onFeedback: (action: FeedbackAction) => void }) {
  const score = Math.round(paper.relevance_score)
  const saved = paper.feedback?.action === 'save'
  const dismissed = paper.feedback?.action === 'dismiss' || paper.feedback?.action === 'skip'
  const matchSignals = paperMatchSignals(paper, 2)
  return (
    <article className={`paper-row ${active ? 'active' : ''} ${dismissed ? 'dismissed' : ''}`} onClick={onSelect} tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') onSelect() }}>
      <div className="paper-row-top">
        <div className={`score-pill ${score >= 90 ? 'excellent' : score >= 80 ? 'good' : ''}`}><strong>{score}</strong><span>推荐</span></div>
        <div className="paper-identifiers">
          {(paper.notable_organizations ?? []).slice(0, 2).map((organization) => <OrganizationBadge key={organization} name={organization} />)}
          {paper.categories.slice(0, 2).map((category) => <span className="category-tag" key={category}>{category}</span>)}
          <span>{paper.arxiv_id}{paper.version ? ` ${paper.version}` : ''}</span>
        </div>
        <time title={new Date(paper.submitted_at).toLocaleString('zh-CN')}>{relativeTime(paper.submitted_at)}</time>
      </div>
      <h2>{paper.title}</h2>
      <p className="authors">{formatAuthors(paper.authors)}</p>
      {(paper.summary || paper.abstract) && <p className="paper-summary">{paper.summary || paper.abstract}</p>}
      <div className="paper-row-foot">
        <div className="match-basis">
          <small>匹配</small>
          {matchSignals.map((reason) => <span key={reason}>{reason}</span>)}
        </div>
        <div className="row-actions">
          <button className={dismissed ? 'selected dislike' : ''} onClick={(event) => { event.stopPropagation(); onFeedback('dismiss') }} aria-label="不感兴趣" title="不感兴趣"><ThumbsDown size={16} /></button>
          <button className={saved ? 'selected' : ''} onClick={(event) => { event.stopPropagation(); onFeedback('save') }} aria-label={saved ? '取消收藏' : '收藏'} title={saved ? '取消收藏' : '收藏'}>
            {saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}
          </button>
          <ChevronRight className="row-chevron" size={17} />
        </div>
      </div>
    </article>
  )
}

function compactSignals(values: Array<string | null | undefined>, limit = 4) {
  const seen = new Set<string>()
  const result: string[] = []
  for (const value of values) {
    const clean = value?.trim()
    const key = clean?.toLocaleLowerCase().replace(/[\s_-]+/g, '')
    if (!clean || !key || seen.has(key)) continue
    seen.add(key)
    result.push(clean)
    if (result.length >= limit) break
  }
  return result
}

function paperMatchSignals(paper: Paper, limit = 4) {
  return compactSignals([
    ...(paper.matched_topics ?? []),
    ...(paper.matched_keywords ?? []),
    ...paper.match_reasons,
  ], limit)
}

function paperAnalysisSignals(paper: Paper, analysis: PaperAnalysis, limit = 4) {
  return compactSignals([...(paper.analysis_tags ?? []), ...(analysis.tags ?? [])], limit)
}

function PaperDetail({ paper, projects, onProjectsChange, onToast, onClose, onFeedback, onSaveReading, onSaveDetailedFeedback }: {
  paper: Paper
  projects: ResearchProject[]
  onProjectsChange: (projects: ResearchProject[]) => void
  onToast: (kind: Toast['kind'], message: string) => void
  onClose: () => void
  onFeedback: (action: FeedbackAction) => void
  onSaveReading: (reading: ReadingData) => Promise<boolean>
  onSaveDetailedFeedback: (feedback: Feedback) => Promise<boolean>
}) {
  const analysis = normalizeAnalysis(paper)
  const saved = paper.feedback?.action === 'save'
  const dismissed = paper.feedback?.action === 'dismiss' || paper.feedback?.action === 'skip'
  const [reading, setReading] = useState<ReadingData>(() => getReading(paper))
  const [feedback, setFeedback] = useState<Feedback>(() => ({ ...paper.feedback, reasons: paper.feedback?.reasons ?? paper.feedback_reasons ?? [] }))
  const [savingWorkflow, setSavingWorkflow] = useState(false)
  const [savingFeedback, setSavingFeedback] = useState(false)
  const [classificationBusy, setClassificationBusy] = useState('')
  const [detailTab, setDetailTab] = useState<'overview' | 'analysis' | 'workflow'>('overview')

  useEffect(() => {
    setReading(getReading(paper))
    setFeedback({ ...paper.feedback, reasons: paper.feedback?.reasons ?? paper.feedback_reasons ?? [] })
    setDetailTab('overview')
  }, [paper.id, paper.reading, paper.reading_state, paper.reading_priority, paper.notes, paper.tags, paper.feedback, paper.feedback_reasons])

  const projectMemberships = paper.project_memberships ?? paper.projects ?? []
  const customLibraries = projects.filter((project) => project.kind !== 'favorites')

  const saveWorkflow = async () => {
    setSavingWorkflow(true)
    await onSaveReading(reading)
    setSavingWorkflow(false)
  }

  const saveFeedback = async () => {
    setSavingFeedback(true)
    await onSaveDetailedFeedback({ ...feedback, action: feedback.action === 'dismiss' ? 'skip' : feedback.action })
    setSavingFeedback(false)
  }

  const addToProject = async (project: ResearchProject) => {
    setClassificationBusy(String(project.id))
    try {
      const membership = isDemo
        ? { id: Date.now(), project_id: project.id, paper_id: paper.id, reading_state: 'queued' as ReadingState, priority: reading.priority, notes: '', tags: reading.tags, paper: projectPaperSummary(paper) }
        : await api.addProjectPaper(project.id, paper.id, { reading_state: 'queued', priority: reading.priority, tags: reading.tags })
      onProjectsChange(projects.map((item) => String(item.id) === String(project.id)
        ? { ...item, paper_count: (item.papers?.length ?? item.paper_count) + 1, papers: [...(item.papers ?? []), { ...membership, project_id: item.id, paper_id: paper.id, paper: membership.paper ?? projectPaperSummary(paper) }] }
        : item))
      onToast('success', `已加入“${project.name}”`)
    } catch (error) {
      onToast('error', error instanceof Error ? error.message : '加入分类库失败')
    } finally { setClassificationBusy('') }
  }

  const removeFromProject = async (membership: ProjectMembership) => {
    const project = projects.find((item) => String(item.id) === String(membership.project_id))
    if (!project) return
    setClassificationBusy(String(project.id))
    try {
      if (!isDemo) await api.removeProjectPaper(project.id, paper.id)
      onProjectsChange(projects.map((item) => String(item.id) === String(project.id)
        ? { ...item, paper_count: Math.max(0, item.paper_count - 1), papers: (item.papers ?? []).filter((link) => String(link.paper_id) !== String(paper.id)) }
        : item))
      onToast('success', `已从“${project.name}”移除`)
    } catch (error) {
      onToast('error', error instanceof Error ? error.message : '移除失败')
    } finally { setClassificationBusy('') }
  }
  const scores = [
    ['相关', analysis.relevance_score ?? paper.relevance_score, '#2d6a4f'],
    ['新颖', analysis.novelty_score ?? paper.novelty_score, '#9a6700'],
    ['方法', analysis.method_score ?? paper.quality_score, '#325ea8'],
    ['证据', analysis.evidence_score ?? paper.quality_score, '#7c4d9e'],
    ['潜力', analysis.potential_score ?? paper.relevance_score, '#b54747'],
  ] as Array<[string, number | null | undefined, string]>
  const analysisSignals = paperAnalysisSignals(paper, analysis)
  const semanticSignals = (paper.semantic_candidate_topics ?? []).slice(0, 4).map((item) => `${item.name} ${Math.round(item.score * 100)}%`)
  const analysisStage = analysis.kind === 'deep'
    ? 'AI 全文深读'
    : analysis.kind === 'screening'
      ? 'AI 摘要初筛'
      : paper.analysis
        ? 'AI 分析'
        : '等待 AI 分析'
  const inputScope = analysis.input_scope === 'html'
    ? 'arXiv HTML'
    : analysis.input_scope === 'pdf'
      ? '论文 PDF'
      : analysis.input_scope === 'abstract'
        ? '标题 + 摘要'
        : analysis.input_scope ?? '尚无模型输入'

  return (
    <article className="paper-detail">
      <div className="detail-mobile-bar">
        <button className="button subtle" onClick={onClose}><ChevronLeft size={17} /> 返回列表</button>
        <span>{paper.arxiv_id}</span>
      </div>
      <header className="detail-header">
        <div className="detail-kicker">
          <span className="analysis-badge"><Sparkles size={13} /> {analysisStage}</span>
          <span className="scope-badge">输入：{inputScope}</span>
          {(paper.notable_organizations ?? []).map((organization) => <OrganizationBadge key={organization} name={organization} />)}
        </div>
        <h2>{paper.title}</h2>
        <p className="detail-authors">{paper.authors.join(' · ')}</p>
        <div className="detail-meta">
          <span><Clock3 size={14} /> {new Date(paper.submitted_at).toLocaleDateString('zh-CN')}</span>
          <span>{paper.arxiv_id}{paper.version ? ` ${paper.version}` : ''}</span>
          <span>{paper.categories.join(' · ')}</span>
        </div>
        <div className="detail-actions">
          <a className="button primary compact" href={paper.pdf_url} target="_blank" rel="noreferrer"><FileText size={15} /> 阅读 PDF <ExternalLink size={13} /></a>
          <a className="button secondary compact" href={paper.abs_url} target="_blank" rel="noreferrer">arXiv 页面 <ExternalLink size={13} /></a>
          <div className="action-divider" />
          <button className={`icon-button labeled ${saved ? 'active' : ''}`} onClick={() => onFeedback('save')} title={saved ? '取消收藏' : '收藏'}>
            {saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}<span>{saved ? '已收藏' : '收藏'}</span>
          </button>
          <button className={`icon-button labeled ${dismissed ? 'danger-active' : ''}`} onClick={() => onFeedback('dismiss')} title="不感兴趣">
            <ThumbsDown size={16} /><span>{dismissed ? '已忽略' : '不感兴趣'}</span>
          </button>
        </div>
      </header>

      <nav className="paper-detail-tabs" aria-label="论文详情视图">
        <button className={detailTab === 'overview' ? 'active' : ''} onClick={() => setDetailTab('overview')}><Sparkles size={14} /> 速览</button>
        <button className={detailTab === 'analysis' ? 'active' : ''} onClick={() => setDetailTab('analysis')}><FileText size={14} /> 深度分析</button>
        <button className={detailTab === 'workflow' ? 'active' : ''} onClick={() => setDetailTab('workflow')}><NotebookTabs size={14} /> 阅读管理{reading.state !== 'unread' && <i />}</button>
      </nav>

      <div className={`detail-scroll detail-view-${detailTab}`}>
        {detailTab === 'overview' && <>
          <section className="decision-summary">
            <div className="decision-copy"><span>一句话结论</span><p>{paper.summary || analysis.overview || '尚未生成中文总结。刷新或等待深度分析任务完成后可在这里查看。'}</p></div>
            <div className="decision-signals">
              <SignalGroup label="AI 确认方向" values={paper.matched_topics ?? []} empty="初筛尚未确认正式方向" />
              <SignalGroup label="语义候选方向" values={semanticSignals} empty="暂无语义候选" />
              <SignalGroup label="分析标签" values={analysisSignals} empty="待深度分析后提取" tone="analysis" />
            </div>
          </section>

          <section className="score-section">
            <div className="five-scores">
              {scores.map(([label, score, color]) => <ScoreGauge key={label} label={label} score={score} color={color} />)}
            </div>
            <p className="recommendation-note"><Sparkles size={15} /> {paper.why_recommended || '已根据关键词、分类与研究画像计算推荐得分。'}</p>
          </section>

          <DetailSection title="核心贡献" pending={!analysis.contributions?.length}>
            {analysis.contributions?.length ? (
              <ol className="contribution-list">{analysis.contributions.map((item, index) => <li key={index}><span>{index + 1}</span><p>{item}</p></li>)}</ol>
            ) : <PendingAnalysis />}
          </DetailSection>

          <DetailSection title="需要留意" pending={!analysis.limitations?.length}>
            {analysis.limitations?.length ? <ul className="limitation-list">{analysis.limitations.map((item, index) => <li key={index}><AlertTriangle size={15} /><span>{item}</span></li>)}</ul> : <PendingAnalysis />}
          </DetailSection>

          <div className="overview-next-actions">
            <button className="button secondary" onClick={() => setDetailTab('analysis')}><FileText size={15} /> 查看完整分析</button>
            <button className="button primary" onClick={() => setDetailTab('workflow')}><NotebookTabs size={15} /> 安排阅读</button>
          </div>
        </>}

        {detailTab === 'analysis' && <>
          {analysis.overview && analysis.overview !== paper.summary && <DetailSection title="论文概览"><p>{analysis.overview}</p></DetailSection>}

          <div className="detail-two-col">
            <DetailSection title="方法" compact pending={!analysis.method}>
              {analysis.method ? <p>{analysis.method}</p> : <PendingAnalysis />}
            </DetailSection>
            <DetailSection title="证据与实验" compact pending={!analysis.evidence}>
              {analysis.evidence ? <p>{analysis.evidence}</p> : <PendingAnalysis />}
            </DetailSection>
          </div>

          <section className="debate-section">
            <div className="debate advocate"><span className="debate-label"><Check size={15} /> 支持者视角</span><p>{analysis.advocate || '等待全文分析后生成支持该工作的最强论据。'}</p></div>
            <div className="debate skeptic"><span className="debate-label"><CircleAlert size={15} /> 怀疑者视角</span><p>{analysis.skeptic || '等待全文分析后生成需要审慎看待的问题。'}</p></div>
          </section>

          <DetailSection title="原始摘要"><p className="original-abstract">{paper.abstract}</p></DetailSection>
          <p className="ai-disclaimer">AI 总结仅用于快速筛选，重要结论请以论文原文为准。</p>
        </>}

        {detailTab === 'workflow' && <>
        <section className="workflow-panel">
          <div className="workflow-heading"><div><span className="section-icon"><NotebookTabs size={15} /></span><div><h3>阅读工作流</h3><p>保存这篇论文的全局阅读进度、优先级与笔记。</p></div></div><button className="button primary compact" onClick={saveWorkflow} disabled={savingWorkflow}><Save size={14} /> {savingWorkflow ? '保存中' : '保存'}</button></div>
          <div className="workflow-grid">
            <label className="field compact-field"><span>阅读状态</span><select value={reading.state} onChange={(event) => setReading({ ...reading, state: event.target.value as ReadingState })}>{READING_STATE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className="field compact-field"><span>优先级</span><select value={reading.priority} onChange={(event) => setReading({ ...reading, priority: Number(event.target.value) })}>{[5, 4, 3, 2, 1].map((value) => <option value={value} key={value}>{value} · {PRIORITY_LABELS[value]}</option>)}</select></label>
          </div>
          <TagEditor label="我的标签" tags={reading.tags} placeholder="例如 基线、待读、可复现" onChange={(tags) => setReading({ ...reading, tags })} />
          <p className="tag-scope-note">这里只保存你自己的整理标签；论文领域与匹配依据由系统单独维护。</p>
          <label className="field workflow-notes"><span>研究笔记</span><textarea rows={3} value={reading.notes} onChange={(event) => setReading({ ...reading, notes: event.target.value })} placeholder="记录需要核查的假设、实验或引用位置" /></label>
          <div className="project-membership-editor">
            <span className="field-label">所属分类库</span>
            <div className="membership-list">
              {projectMemberships.map((membership) => { const library = projects.find((item) => String(item.id) === String(membership.project_id)); return <span className="membership-chip" key={String(membership.project_id)} style={{ '--project-color': membership.project_color ?? library?.color ?? '#63706c' } as React.CSSProperties}><i />{membership.project_name ?? library?.name ?? '分类库'}{library?.kind === 'favorites' ? <BookmarkCheck size={12} /> : <button onClick={() => void removeFromProject(membership)} aria-label="从分类库移除"><X size={12} /></button>}</span> })}
              {!projectMemberships.length && <small>尚未加入任何分类库；点击“收藏”会自动进入收藏库。</small>}
            </div>
            <div className="classification-picker">
              {customLibraries.map((library) => { const membership = projectMemberships.find((item) => String(item.project_id) === String(library.id)); const busy = classificationBusy === String(library.id); return <label key={library.id} className={membership ? 'selected' : ''}><input type="checkbox" checked={Boolean(membership)} disabled={Boolean(classificationBusy)} onChange={() => { if (membership) void removeFromProject(membership); else void addToProject(library) }} /><i style={{ background: library.color }} /><span>{library.name}</span><small>{busy ? '更新中' : membership ? '已加入' : '自动加入'}</small></label> })}
              {!customLibraries.length && <small>在“分类库”中新建分类后，可以在这里一键归档。</small>}
            </div>
          </div>
        </section>
        <section className="feedback-panel">
          <div className="workflow-heading"><div><span className="section-icon"><ThumbsDown size={15} /></span><div><h3>推荐反馈</h3><p>原因会用于校准后续推荐，不会改变论文原始评分。</p></div></div><button className="button secondary compact" onClick={saveFeedback} disabled={savingFeedback}><Save size={14} /> {savingFeedback ? '保存中' : '保存反馈'}</button></div>
          <div className="rating-row"><span>整体评价</span><div>{[1, 2, 3, 4, 5].map((value) => <button className={(feedback.rating ?? 0) >= value ? 'active' : ''} key={value} onClick={() => setFeedback({ ...feedback, rating: feedback.rating === value ? null : value })} aria-label={`${value} 星`}><Star size={17} fill={(feedback.rating ?? 0) >= value ? 'currentColor' : 'none'} /></button>)}</div></div>
          <div className="feedback-reasons" aria-label="反馈原因">{FEEDBACK_REASON_OPTIONS.map(([value, label]) => { const active = feedback.reasons?.includes(value); return <button key={value} className={active ? 'active' : ''} onClick={() => setFeedback({ ...feedback, action: feedback.action ?? 'dislike', reasons: active ? feedback.reasons?.filter((item) => item !== value) : [...(feedback.reasons ?? []), value] })}>{active && <Check size={12} />}{label}</button> })}</div>
          <label className="field workflow-notes"><span>补充说明</span><textarea rows={2} value={feedback.note ?? ''} onChange={(event) => setFeedback({ ...feedback, note: event.target.value })} placeholder="例如：真实实验规模不足，暂不进入阅读队列" /></label>
        </section>
        </>}
      </div>
    </article>
  )
}

function normalizeAnalysis(paper: Paper): PaperAnalysis {
  return paper.analysis ?? {}
}

function ScoreGauge({ label, score, color }: { label: string; score?: number | null; color: string }) {
  const value = score == null ? null : Math.round(score)
  return (
    <div className="score-gauge">
      <div className="gauge-track" style={{ '--score': `${value ?? 0}%`, '--gauge-color': color } as React.CSSProperties}>
        <span>{value ?? '—'}</span>
      </div>
      <small>{label}</small>
    </div>
  )
}

function DetailSection({ title, children, compact = false, pending = false }: { title: string; children: ReactNode; compact?: boolean; pending?: boolean }) {
  return (
    <section className={`detail-section ${compact ? 'compact' : ''} ${pending ? 'pending' : ''}`}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

function PendingAnalysis() {
  return <p className="pending-copy"><Clock3 size={14} /> 等待深度分析</p>
}

function SignalGroup({ label, values, empty, tone = 'match' }: { label: string; values: string[]; empty: string; tone?: 'match' | 'analysis' }) {
  return <div className={`signal-group ${tone}`}><span>{label}</span><div>{values.length ? values.map((value) => <em key={value}>{value}</em>) : <small>{empty}</small>}</div></div>
}

function DigestView({ data, state, error, onRetry, onToast }: { data: DashboardData | null; state: LoadState; error: string; onRetry: () => void; onToast: (kind: Toast['kind'], message: string) => void }) {
  const [tab, setTab] = useState<'report' | 'preview' | 'plain'>('report')
  const [report, setReport] = useState<DailyReport | null>(null)
  const [reportState, setReportState] = useState<LoadState>('loading')
  const [reportError, setReportError] = useState('')
  const [reportHistory, setReportHistory] = useState<DailyReportArchiveItem[]>([])
  const [selectedReportDate, setSelectedReportDate] = useState(() => new URLSearchParams(window.location.search).get('date') ?? data?.latest_digest?.digest_date ?? '')
  const [regenerating, setRegenerating] = useState(false)
  const [synthesizing, setSynthesizing] = useState(false)
  const [sendingEmail, setSendingEmail] = useState(false)
  const latestCompleteDate = data?.latest_digest?.digest_date ?? data?.date ?? ''
  const activeReportDate = selectedReportDate || latestCompleteDate
  const activeReport = report?.report_date === activeReportDate ? report : null
  const digestText = activeReport ? buildReportText(activeReport, data?.items ?? []) : ''
  const activeDateMissingFromHistory = Boolean(activeReportDate && !reportHistory.some((item) => item.report_date === activeReportDate))

  const loadReport = useCallback(async () => {
    const reportDate = selectedReportDate || latestCompleteDate
    if (!reportDate) return
    setReportState('loading'); setReportError('')
    try {
      setReport(isDemo ? { ...sampleDailyReport, report_date: reportDate, persisted: true } : await api.dailyReport(reportDate))
      setReportState('ready')
    } catch (loadError) {
      setReport(null); setReportState('error')
      setReportError(loadError instanceof Error ? loadError.message : '结构化日报加载失败')
    }
  }, [latestCompleteDate, selectedReportDate])

  useEffect(() => { void loadReport() }, [loadReport])
  useEffect(() => {
    if (!selectedReportDate && latestCompleteDate) setSelectedReportDate(latestCompleteDate)
  }, [latestCompleteDate, selectedReportDate])
  useEffect(() => {
    if (isDemo) {
      setReportHistory([{ id: 1, report_date: sampleDailyReport.report_date, title: sampleDailyReport.title ?? '当日研究总结', generated_at: new Date().toISOString(), coverage: sampleDailyReport.coverage }])
      return
    }
    api.dailyReportHistory().then(setReportHistory).catch(() => setReportHistory([]))
  }, [report?.snapshot_id])

  const regenerateReport = async () => {
    const reportDate = selectedReportDate || latestCompleteDate
    if (!reportDate) return
    setRegenerating(true)
    try {
      const next = isDemo ? { ...sampleDailyReport, report_date: reportDate, persisted: true, updated_at: new Date().toISOString() } : await api.regenerateDailyReport(reportDate)
      setReport(next)
      if (!isDemo) setReportHistory(await api.dailyReportHistory())
      onToast('success', `${reportDate} 的研究日报已重新生成并保存`)
    } catch (regenerateError) {
      onToast('error', regenerateError instanceof Error ? regenerateError.message : '日报重新生成失败')
    } finally {
      setRegenerating(false)
    }
  }

  const copyDigest = async () => {
    if (!digestText) return
    try {
      await navigator.clipboard.writeText(digestText)
      onToast('success', '简报已复制到剪贴板')
    } catch {
      onToast('error', '浏览器未允许访问剪贴板')
    }
  }

  const synthesizeReport = async () => {
    if (!activeReportDate || isDemo) return
    setSynthesizing(true)
    try {
      const next = await api.synthesizeDailyReport(activeReportDate, true)
      setReport(next)
      const status = next.ai_synthesis?.status
      if (status === 'ready') onToast('success', `${activeReportDate} 的 AI 综合研判已生成并保存`)
      else if (status === 'not_configured') onToast('error', '尚未配置模型 API Key，确定性日报仍可正常使用')
      else if (status === 'insufficient_data') onToast('error', '当天还没有足够的已分析论文，暂不调用 AI')
      else onToast('error', next.ai_synthesis?.last_error ?? 'AI 综合研判生成失败')
    } catch (synthesisError) {
      onToast('error', synthesisError instanceof Error ? synthesisError.message : 'AI 综合研判生成失败')
    } finally {
      setSynthesizing(false)
    }
  }

  const downloadDigest = () => {
    if (!digestText) return
    const url = URL.createObjectURL(new Blob([digestText], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `arxiv-digest-${activeReport?.report_date ?? 'today'}.md`
    link.click()
    URL.revokeObjectURL(url)
    onToast('success', 'Markdown 简报已下载')
  }

  const sendReport = async () => {
    if (!activeReportDate || isDemo) return
    setSendingEmail(true)
    try {
      const result = await api.sendDailyReport(activeReportDate)
      if (!result.ok) throw new Error(`邮件发送失败（${result.email_failed} 个收件人）`)
      onToast('success', `日报已发送给 ${result.email_sent} 个收件人`)
    } catch (sendError) {
      onToast('error', sendError instanceof Error ? sendError.message : '日报邮件发送失败')
    } finally { setSendingEmail(false) }
  }

  if (state === 'loading' && !data) return <WorkspaceSkeleton />
  if (state === 'error') return <PageError message={error} onRetry={onRetry} />

  return (
    <div className="standard-page digest-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow-row"><Mail size={14} /> 每日自动整理</div>
          <h1>研究日报</h1>
          <p>每天独立保存一份研究快照，可按日期回看研究方向、证据、缺口和阅读队列。</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={() => void sendReport()} disabled={!activeReport || sendingEmail || isDemo}><Mail size={16} /> {sendingEmail ? '发送中' : '发送邮件'}</button>
          <button className="button secondary" onClick={copyDigest} disabled={!activeReport}><Copy size={16} /> 复制</button>
          <button className="button primary" onClick={downloadDigest} disabled={!activeReport}><Download size={16} /> 下载 .md</button>
        </div>
      </section>

      <div className="digest-layout">
        <aside className="digest-sidebar">
          <h2>日报档案</h2>
          <div className="report-archive-list">
            {reportHistory.length ? reportHistory.map((item) => <button className={item.report_date === activeReportDate ? 'active' : ''} key={item.id} onClick={() => setSelectedReportDate(item.report_date)}><CalendarDays size={14} /><span><strong>{formatArchiveDate(item.report_date)}</strong><small>{item.coverage?.paper_count ?? 0} 篇 · {item.coverage?.analyzed_paper_count ?? 0} 篇已分析{item.report_date > latestCompleteDate ? ' · 待日更' : ''}</small></span><ChevronRight size={14} /></button>) : <p className="empty-archive">完整每日流程结束后会按日期保存。</p>}
          </div>
          <h2 className="schedule-title">日报规则</h2>
          <dl>
            <div><dt>自动任务</dt><dd>Windows 计划任务</dd></div>
            <div><dt>应用记录时间</dt><dd>{data?.settings?.schedule_time ?? '13:30'}</dd></div>
            <div><dt>时区</dt><dd>{data?.settings?.timezone ?? 'Asia/Shanghai'}</dd></div>
            <div><dt>统计口径</dt><dd>正式研究方向</dd></div>
            <div><dt>代表论文</dt><dd>最多 10 篇</dd></div>
          </dl>
          <div className="delivery-note"><Save size={16} /><span>每日任务结束后自动保存；启用 SMTP 后自动发送，也可手动重发所选日期。</span></div>
        </aside>

        <section className="digest-preview-wrap">
          <div className="preview-toolbar">
            <div className="segmented-control">
              <button className={tab === 'report' ? 'active' : ''} onClick={() => setTab('report')}>研究总结</button>
              <button className={tab === 'preview' ? 'active' : ''} onClick={() => setTab('preview')}>摘要版</button>
              <button className={tab === 'plain' ? 'active' : ''} onClick={() => setTab('plain')}>纯文本</button>
            </div>
            <div className="report-snapshot-meta">
              <span className="desktop-report-date">{formatFullDate(activeReportDate)}</span>
              <label className="mobile-report-date">
                <CalendarDays size={13} />
                <select aria-label="选择历史日报" value={activeReportDate} onChange={(event) => setSelectedReportDate(event.target.value)}>
                  {activeDateMissingFromHistory && <option value={activeReportDate}>{formatArchiveDate(activeReportDate)}</option>}
                  {reportHistory.map((item) => <option key={item.id} value={item.report_date}>{formatArchiveDate(item.report_date)} · {item.coverage?.paper_count ?? 0} 篇</option>)}
                </select>
                <ChevronDown size={12} />
              </label>
              {activeReport?.generated_at && <small>保存于 {formatDateTime(activeReport.generated_at)}</small>}
              <button className="button secondary compact" onClick={() => void synthesizeReport()} disabled={!activeReport || synthesizing || isDemo} title="调用当前 Provider，跨论文生成并单独保存长篇综合研判"><Sparkles className={synthesizing ? 'spin' : ''} size={13} /> {synthesizing ? '研判中' : activeReport?.ai_synthesis?.overall_assessment ? '重做 AI 研判' : '生成 AI 研判'}</button>
              <button className="button subtle compact" onClick={() => void regenerateReport()} disabled={regenerating}><RefreshCw className={regenerating ? 'spin' : ''} size={13} /> {regenerating ? '生成中' : '重新生成'}</button>
            </div>
          </div>
          {reportState === 'loading' && <div className="report-loading"><LoaderCircle className="spin" size={22} /><span>正在读取 {formatArchiveDate(activeReportDate)} 的研究快照</span></div>}
          {reportState !== 'loading' && !activeReport && <div className="report-error"><CircleAlert size={20} /><div><strong>结构化日报暂不可用</strong><p>{reportError}</p><small>论文列表仍可正常浏览；这里不会用演示内容冒充接口结果。</small></div><button className="button secondary compact" onClick={() => void loadReport()}><RefreshCw size={14} /> 重试</button></div>}
          {reportState !== 'loading' && activeReport && tab === 'report' && <StructuredReport report={activeReport} papers={data?.items ?? []} />}
          {reportState !== 'loading' && activeReport && tab === 'preview' && <EmailPreview report={activeReport} />}
          {reportState !== 'loading' && activeReport && tab === 'plain' && <pre className="plain-preview">{digestText}</pre>}
        </section>
      </div>
    </div>
  )
}

function StructuredReport({ report, papers }: { report: DailyReport; papers: Paper[] }) {
  const findPaper = (paperId: Paper['id']) => papers.find((paper) => String(paper.id) === String(paperId))
  return (
    <article className="structured-report">
      <header>
        <span>执行摘要</span>
        <h2>{report.title ?? `${report.report_date} 当日研究总结`}</h2>
        {report.incomplete && <div className="report-incomplete"><AlertTriangle size={16} /><strong>初筛未完全完成</strong><span>仍有 {report.coverage?.screening_failed ?? report.screening_status?.failed ?? 0} 篇失败，缺失论文未按零相关处理。</span></div>}
        <p>{report.executive_summary}</p>
        <div>
          {report.coverage && <>
            <em>{report.coverage.paper_count} 篇正式方向论文</em>
            {report.coverage.candidate_paper_count !== undefined && <em>{report.coverage.candidate_paper_count} 篇抓取候选</em>}
            <em>{report.coverage.analyzed_paper_count ?? 0} 篇已分析</em>
            <em>{report.coverage.deep_analysis_count ?? 0} 篇深读</em>
          </>}
        </div>
      </header>

      {report.research_situation && <ResearchSituation situation={report.research_situation} />}

      <AiSynthesisSection synthesis={report.ai_synthesis} />

      <section className="report-section">
        <div className="report-section-title"><TrendingUp size={16} /><h3>研究方向动态</h3></div>
        <div className="insight-list">
          {report.topic_insights.map((insight) => (
            <article key={insight.topic_id ?? insight.topic}>
              <span className={`direction-dot ${insight.direction ?? 'stable'}`} />
              <div><strong>{insight.topic}<em>{insight.paper_count} 篇</em></strong><p>{insight.insight}</p></div>
            </article>
          ))}
        </div>
      </section>

      <section className="report-section">
        <div className="report-section-title"><Sparkles size={16} /><h3>代表论文</h3></div>
        <div className="notable-list">
          {report.highlight_papers.map((item, index) => {
            const paper = findPaper(item.paper_id)
            const organizations = item.notable_organizations ?? paper?.notable_organizations ?? []
            return (
              <article key={String(item.paper_id)}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <div><strong>{Math.round(item.score ?? paper?.relevance_score ?? 0)} 分</strong><em>{item.recommendation ?? '重点关注'}</em></div>
                  {organizations.length > 0 && <div className="report-organizations">{organizations.map((organization) => <OrganizationBadge key={organization} name={organization} />)}</div>}
                  <h4>{item.title}</h4>
                  <p>{item.summary ?? paper?.summary ?? item.why_it_matters ?? ''}</p>
                  {(paper?.abs_url || item.arxiv_id) && <a href={paper?.abs_url ?? `https://arxiv.org/abs/${item.arxiv_id}`} target="_blank" rel="noreferrer">查看论文 <ExternalLink size={12} /></a>}
                </div>
              </article>
            )
          })}
        </div>
      </section>

      <div className="report-two-col">
        <ReportEvidence title="共同方法" icon={<Network size={15} />} items={report.common_methods} />
        <ReportEvidence title="证据形态" icon={<CheckCircle2 size={15} />} items={report.common_evidence} />
      </div>

      <section className="report-section queue-section">
        <div className="report-section-title"><Library size={16} /><h3>推荐阅读队列</h3></div>
        <div>
          {report.suggested_reading_queue.map((item) => (
            <article key={`${item.rank}-${item.paper_id}`}>
              <span>{item.rank}</span>
              <div><h4>{item.title}</h4><p>{item.next_action ?? item.reason}</p></div>
              <div><strong>{READING_QUEUE_LABELS[item.priority] ?? item.priority}</strong><small>{item.estimated_minutes ? `${item.estimated_minutes} 分钟` : ''}</small></div>
            </article>
          ))}
        </div>
      </section>

      {report.methodology_note && <footer><CircleAlert size={14} /> {report.methodology_note}</footer>}
    </article>
  )
}

function AiSynthesisSection({ synthesis }: { synthesis?: DailyReport['ai_synthesis'] }) {
  const hasSavedContent = Boolean(synthesis?.overall_assessment)
  const statusCopy: Record<string, { title: string; detail: string }> = {
    not_configured: { title: '尚未配置 AI 综合研判', detail: '配置模型 API Key 后，可在日报顶部手动生成；打开或切换日期不会调用模型。' },
    insufficient_data: { title: '当天分析证据不足', detail: '至少需要一篇已完成分析并进入代表列表的论文，系统才会调用 AI。' },
    disabled: { title: '自动 AI 综合研判已关闭', detail: '确定性日报保持可用，也可以在日报顶部按需手动生成。' },
    error: { title: '最近一次 AI 综合研判失败', detail: synthesis?.last_error ?? '已保留确定性日报和此前成功内容，可稍后重试。' },
  }
  if (!synthesis) {
    return <section className="report-section ai-synthesis-section empty"><div className="report-section-title"><Sparkles size={16} /><h3>AI 综合研判</h3></div><div className="ai-synthesis-status"><strong>尚未生成长篇综合研判</strong><p>日更完成后可自动生成，也可使用日报顶部按钮按需调用一次。</p></div></section>
  }
  const status = statusCopy[synthesis.status]
  return <section className={`report-section ai-synthesis-section ${synthesis.stale ? 'stale' : ''}`}>
    <div className="report-section-title"><Sparkles size={16} /><h3>AI 综合研判</h3>{synthesis.budget_mode && <span>{synthesis.budget_mode}</span>}</div>
    {(status || synthesis.stale) && <div className={`ai-synthesis-status ${synthesis.status === 'error' ? 'error' : ''}`}><strong>{synthesis.stale ? '底层日报已经变化，这份研判需要更新' : status?.title}</strong><p>{synthesis.stale ? '旧内容仍保留供对照；点击日报顶部“重做 AI 研判”才会再次调用模型。' : status?.detail}</p></div>}
    {hasSavedContent && <div className="ai-synthesis-content">
      <header><span>总体判断</span><h4>{synthesis.headline}</h4><p>{synthesis.overall_assessment}</p></header>
      {Boolean(synthesis.core_threads?.length) && <section><h5>核心研究主线</h5><div className="ai-thread-list">{synthesis.core_threads?.map((thread, index) => <article key={`${thread.title}-${index}`}><div><span>{String(index + 1).padStart(2, '0')}</span><h6>{thread.title}</h6></div><p>{thread.analysis}</p><footer>{thread.paper_ids.map((paperId) => <a key={String(paperId)} href={`?view=library&paper=${encodeURIComponent(String(paperId))}`}>论文 #{paperId}</a>)}</footer></article>)}</div></section>}
      <div className="ai-analysis-grid">
        <section><h5>方法路线比较</h5><p>{synthesis.method_comparison}</p></section>
        <section><h5>证据质量审计</h5><p>{synthesis.evidence_assessment}</p></section>
        <section><h5>分歧、风险与未解决问题</h5><p>{synthesis.disagreements_and_risks}</p></section>
        <section><h5>未来研究路线</h5><p>{synthesis.research_outlook}</p></section>
      </div>
      {Boolean(synthesis.recommended_actions?.length) && <section className="ai-actions"><h5>建议行动</h5><ol>{synthesis.recommended_actions?.map((action, index) => <li key={`${index}-${action}`}>{action}</li>)}</ol></section>}
      <footer><span>{synthesis.model ?? synthesis.provider ?? '已配置模型'} · {synthesis.generated_at ? formatDateTime(synthesis.generated_at) : '已保存'}</span><span>{synthesis.usage?.available ? `${formatInteger(synthesis.usage.total_tokens ?? 0)} Token` : 'Provider 未返回 usage'}</span></footer>
    </div>}
  </section>
}

function OrganizationBadge({ name }: { name: string }) {
  return <span className="organization-badge" title="重点机构，依据 arXiv 明确提供的作者单位识别"><Building2 size={11} /> {name}</span>
}

function ResearchSituation({ situation }: { situation: NonNullable<DailyReport['research_situation']> }) {
  const volumeChange = situation.volume.change
  const evidenceLabels: Record<string, string> = { no_signal: '暂无信号', insufficient_analysis: '分析不足', preliminary: '初步', mixed: '混合证据', strong: '较强证据' }
  const overview = situation.quality_overview
  return <section className="report-section situation-section"><div className="report-section-title"><Radar size={16} /><h3>当日研究态势</h3></div><p className="situation-synthesis">{situation.synthesis}</p><div className="situation-grid"><article><span>研究活动</span><strong>{situation.volume.today} 篇</strong><small>较昨日 {volumeChange > 0 ? '+' : ''}{volumeChange} · 近 7 日均值 {situation.volume.recent_daily_average}</small></article><article><span>方向变化</span><strong>{situation.direction_dynamics.rising.length ? situation.direction_dynamics.rising.join('、') : '暂无上升方向'}</strong><small>{situation.direction_dynamics.falling.length ? `回落：${situation.direction_dynamics.falling.join('、')}` : `头部方向占 ${Math.round(situation.direction_dynamics.top_direction_share_percent)}%`}</small></article><article><span>共同方法</span><strong>{situation.method_signals.length ? situation.method_signals.join('、') : '尚未形成稳定共性'}</strong><small>跨论文方法信号</small></article><article><span>证据成熟度</span><strong>{evidenceLabels[situation.evidence_maturity.status] ?? situation.evidence_maturity.status}</strong><small>{situation.evidence_maturity.assessment}</small></article></div>{overview && <QualityOverview overview={overview} />}</section>
}

function QualityOverview({ overview }: { overview: NonNullable<NonNullable<DailyReport['research_situation']>['quality_overview']> }) {
  return <div className="quality-overview"><header><div><span>优质论文综合总览</span><h4>今日高分论文推进了什么</h4></div><small>推荐分 ≥ {Math.round(overview.score_threshold)} · 纳入 {overview.included_paper_count}/{overview.qualified_paper_count} 篇</small></header><p className="quality-overview-lead">{overview.lead}</p>{overview.main_threads.length > 0 && <div className="quality-threads">{overview.main_threads.map((thread) => <span key={thread.name}><strong>{thread.name}</strong><em>{thread.paper_count} 篇{thread.average_score != null ? ` · 均分 ${thread.average_score}` : ''}</em></span>)}</div>}<div className="quality-findings">{overview.paper_findings.map((item, index) => <article key={String(item.paper_id)}><div className="quality-finding-heading"><span>{String(index + 1).padStart(2, '0')}</span><div><h5>{item.title}</h5><small>{Math.round(item.score)} 分 · {item.analysis_kind === 'deep' ? '全文深读' : item.analysis_kind === 'screening' ? '摘要初筛' : '本地线索'}</small></div></div><p>{item.contribution}</p><dl><div><dt>路线</dt><dd>{item.method}</dd></div><div><dt>证据</dt><dd>{item.evidence}</dd></div><div><dt>留意</dt><dd>{item.limitation}</dd></div></dl></article>)}</div><div className="quality-conclusion"><p><strong>横向比较</strong>{overview.comparison}</p><p><strong>证据判断</strong>{overview.evidence_conclusion}</p></div></div>
}

const READING_QUEUE_LABELS: Record<string, string> = { must_read: '必读', read: '精读', skim: '速览', skip: '暂存' }

function ReportEvidence({ title, icon, items }: { title: string; icon: ReactNode; items: DailyAggregateItem[] }) {
  return <section className="report-section evidence-section"><div className="report-section-title">{icon}<h3>{title}</h3></div><div>{items.map((item, index) => { const label = item.name ?? item.item ?? '未命名线索'; return <span key={`${label}-${index}`}><strong>{label}</strong><em>{item.count} 篇</em></span> })}</div></section>
}

function EmailPreview({ report }: { report: DailyReport }) {
  const highlights = report.highlight_papers.slice(0, 5)
  return (
    <div className="email-canvas">
      <header>
        <div className="email-logo"><img src={BRAND_LOGO} alt="XivPulse" /></div>
        <span>{formatFullDate(report.report_date)}</span>
      </header>
      <div className="email-intro">
        <span>当日研究摘要版</span>
        <h2>当日有 {report.coverage?.high_score_paper_count ?? highlights.length} 篇论文值得关注</h2>
        <p>共发现 {report.coverage?.candidate_paper_count ?? report.coverage?.paper_count ?? 0} 篇候选，其中 {report.coverage?.paper_count ?? 0} 篇可归入正式研究方向，{report.coverage?.analyzed_paper_count ?? 0} 篇已有分析。</p>
      </div>
      <div className="email-stats">
        {report.topic_insights.slice(0, 4).map((item) => <span key={item.topic_id ?? item.topic}><strong>{item.paper_count}</strong>{item.topic}</span>)}
      </div>
      <div className="email-papers">
        {highlights.length ? highlights.map((paper, index) => (
          <article key={String(paper.paper_id)}>
            <div className="email-rank">{String(index + 1).padStart(2, '0')}</div>
            <div>
              <div className="email-paper-meta"><strong>{Math.round(paper.score ?? 0)} 分</strong><span>{paper.arxiv_id ?? paper.recommendation ?? '重点关注'}</span></div>
              <h3>{paper.title}</h3>
              <p>{paper.summary || paper.why_it_matters || '建议打开论文核查方法与证据。'}</p>
              <small>{paper.why_it_matters || paper.recommendation || '已进入当日推荐列表'}</small>
            </div>
          </article>
        )) : <EmptyPapers filtered={false} />}
      </div>
      <footer>本地摘要预览，不会自动发送 · AI 总结仅用于论文筛选</footer>
    </div>
  )
}

const EMPTY_PROJECT: ProjectDraft = { name: '', description: '', goal: '', status: 'active', color: '#1f7a5a', due_date: null }

function ProjectsView({ projects, onProjectsChange, onPapersReload, onOpenPaper, onToast }: {
  projects: ResearchProject[]
  onProjectsChange: (projects: ResearchProject[]) => void
  onPapersReload: () => void
  onOpenPaper: (paperId: Paper['id']) => void
  onToast: (kind: Toast['kind'], message: string) => void
}) {
  const [selectedId, setSelectedId] = useState<string>(() => String(projects[0]?.id ?? ''))
  const [draft, setDraft] = useState<ProjectDraft>(EMPTY_PROJECT)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [paperQuery, setPaperQuery] = useState('')
  const [paperResults, setPaperResults] = useState<Paper[]>([])
  const [addPaperId, setAddPaperId] = useState('')
  const [membershipEdit, setMembershipEdit] = useState<ProjectMembership | null>(null)
  const [loadState, setLoadState] = useState<LoadState>(projects.length || isDemo ? 'ready' : 'loading')
  const [error, setError] = useState('')

  const selected = projects.find((project) => String(project.id) === selectedId) ?? projects[0] ?? null

  useEffect(() => {
    if (!selectedId && projects[0]) setSelectedId(String(projects[0].id))
    if (selectedId && !projects.some((project) => String(project.id) === selectedId)) setSelectedId(String(projects[0]?.id ?? ''))
  }, [projects, selectedId])

  useEffect(() => {
    if (projects.length || isDemo) { setLoadState('ready'); return }
    let active = true
    void api.projects().then(async (listed) => {
      const detailed = await Promise.all(listed.map(async (project) => {
        try { return await api.project(project.id) } catch { return project }
      }))
      if (active) { onProjectsChange(detailed); setLoadState('ready'); setError('') }
    }).catch((loadError) => { if (active) { setLoadState('error'); setError(loadError instanceof Error ? loadError.message : '分类库加载失败') } })
    return () => { active = false }
  }, [onProjectsChange, projects.length])

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(() => {
      if (isDemo) { setPaperResults(sampleDashboard.items.slice(0, 30)); return }
      void api.papers({ q: paperQuery.trim() || undefined, limit: 30 })
        .then((result) => { if (active) setPaperResults(result.items) })
        .catch(() => { if (active) setPaperResults([]) })
    }, paperQuery.trim() ? 250 : 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [paperQuery])

  const openCreate = () => { setDraft(EMPTY_PROJECT); setCreating(true); setEditing(false) }
  const openEdit = () => {
    if (!selected || selected.is_system) return
    setDraft({ name: selected.name, description: selected.description, goal: selected.goal, status: selected.status, color: selected.color, due_date: selected.due_date ?? null })
    setEditing(true); setCreating(false)
  }

  const saveProject = async (event: FormEvent) => {
    event.preventDefault()
    if (!draft.name.trim()) return
    setSaving(true)
    try {
      const saved = isDemo
        ? editing && selected
          ? { ...selected, ...draft, name: draft.name.trim(), updated_at: new Date().toISOString() }
          : { ...draft, kind: 'custom', is_system: false, id: Date.now(), name: draft.name.trim(), paper_count: 0, papers: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString() } as ResearchProject
        : editing && selected ? await api.updateProject(selected.id, draft) : await api.createProject(draft)
      const next = editing && selected ? projects.map((item) => String(item.id) === String(selected.id) ? saved : item) : [...projects, saved]
      onProjectsChange(next)
      setSelectedId(String(saved.id))
      setCreating(false); setEditing(false)
      onToast('success', editing ? '分类库已更新' : '分类库已创建')
    } catch (saveError) {
      onToast('error', saveError instanceof Error ? saveError.message : '分类库保存失败')
    } finally { setSaving(false) }
  }

  const deleteProject = async () => {
    if (!selected || selected.is_system || !window.confirm(`删除分类库“${selected.name}”？论文不会从论文库删除。`)) return
    try {
      if (!isDemo) await api.deleteProject(selected.id)
      onProjectsChange(projects.filter((item) => String(item.id) !== String(selected.id)))
      onToast('success', '分类库已删除，论文仍保留在论文库')
    } catch (deleteError) { onToast('error', deleteError instanceof Error ? deleteError.message : '分类库删除失败') }
  }

  const availablePapers = paperResults.filter((paper) => !selected?.papers?.some((membership) => String(membership.paper_id) === String(paper.id)))

  const addPaper = async () => {
    if (!selected || !addPaperId) return
    const paper = paperResults.find((item) => String(item.id) === addPaperId)
    if (!paper) return
    try {
      const membership = isDemo
        ? { id: Date.now(), project_id: selected.id, paper_id: paper.id, reading_state: 'queued' as ReadingState, priority: getReading(paper).priority, notes: '', tags: getReading(paper).tags, paper: projectPaperSummary(paper) }
        : await api.addProjectPaper(selected.id, paper.id, { reading_state: 'queued', priority: getReading(paper).priority, tags: getReading(paper).tags })
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, paper_count: project.paper_count + 1, papers: [...(project.papers ?? []), { ...membership, paper: membership.paper ?? projectPaperSummary(paper) }] } : project))
      setAddPaperId(''); setPaperQuery('')
      if (selected.kind === 'favorites') onPapersReload()
      onToast('success', `论文已加入“${selected.name}”`)
    } catch (addError) { onToast('error', addError instanceof Error ? addError.message : '加入分类库失败') }
  }

  const removePaper = async (membership: ProjectMembership) => {
    if (!selected) return
    try {
      if (!isDemo) await api.removeProjectPaper(selected.id, membership.paper_id)
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, paper_count: Math.max(0, project.paper_count - 1), papers: (project.papers ?? []).filter((item) => String(item.paper_id) !== String(membership.paper_id)) } : project))
      if (selected.kind === 'favorites') onPapersReload()
      onToast('success', '论文已从分类库移除')
    } catch (removeError) { onToast('error', removeError instanceof Error ? removeError.message : '移除论文失败') }
  }

  const saveMembership = async () => {
    if (!selected || !membershipEdit) return
    try {
      const payload = { reading_state: membershipEdit.reading_state, priority: membershipEdit.priority, notes: membershipEdit.notes, tags: membershipEdit.tags }
      const saved = isDemo ? membershipEdit : await api.updateProjectPaper(selected.id, membershipEdit.paper_id, payload)
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, papers: (project.papers ?? []).map((item) => String(item.paper_id) === String(saved.paper_id) ? { ...item, ...saved, paper: item.paper ?? saved.paper } : item) } : project))
      setMembershipEdit(null)
      onToast('success', '分类库内阅读信息已保存')
    } catch (editError) { onToast('error', editError instanceof Error ? editError.message : '保存失败') }
  }

  if (loadState === 'loading') return <WorkspaceSkeleton />
  if (loadState === 'error' && !projects.length) return <PageError message={error} onRetry={() => window.location.reload()} />

  return (
    <div className="standard-page projects-page">
      <section className="page-heading"><div><div className="eyebrow-row"><NotebookTabs size={14} /> 论文归档</div><h1>分类库</h1><p>收藏论文自动进入收藏库，也可以按课题、方法或用途建立自己的分类。</p></div><button className="button primary" onClick={openCreate}><Plus size={16} /> 新建分类</button></section>
      <div className="project-workbench">
        <aside className="project-list-pane">
          <div className="pane-title"><span>{projects.length} 个分类库</span></div>
          <div className="project-list">{projects.map((project) => <button key={project.id} className={String(project.id) === String(selected?.id) ? 'active' : ''} onClick={() => setSelectedId(String(project.id))}><i style={{ background: project.color }} /><div><strong>{project.name}</strong><span>{project.kind === 'favorites' ? '系统分类' : '自定义分类'} · {project.paper_count} 篇</span></div><ChevronRight size={16} /></button>)}</div>
        </aside>
        <section className="project-detail-pane">
          {selected ? <>
            <header className="project-header"><div className="project-title"><i style={{ background: selected.color }} /><div><span>{selected.kind === 'favorites' ? '系统分类' : '自定义分类'}</span><h2>{selected.name}</h2><p>{selected.description || '用于整理一组相关论文。'}</p></div></div>{!selected.is_system && <div className="project-header-actions"><button className="icon-button" title="编辑分类库" onClick={openEdit}><SettingsIcon size={16} /></button><button className="icon-button danger" title="删除分类库" onClick={() => void deleteProject()}><Trash2 size={16} /></button></div>}</header>
            <div className="project-facts"><div><span>收录论文</span><strong>{selected.paper_count} 篇</strong></div><div><span>正在阅读</span><strong>{(selected.papers ?? []).filter((item) => item.reading_state === 'reading').length} 篇</strong></div><div><span>已处理</span><strong>{(selected.papers ?? []).filter((item) => ['read', 'reproduce', 'cited', 'archived'].includes(item.reading_state)).length} 篇</strong></div></div>
            <div className="project-paper-tools"><label className="search-box"><Search size={16} /><input value={paperQuery} onChange={(event) => { setPaperQuery(event.target.value); setAddPaperId('') }} placeholder="搜索全部已初筛论文" /></label><select value={addPaperId} onChange={(event) => setAddPaperId(event.target.value)}><option value="">{availablePapers.length ? '选择一篇论文' : '没有可加入的论文'}</option>{availablePapers.map((paper) => <option value={String(paper.id)} key={paper.id}>{paper.arxiv_id} · {paper.title}</option>)}</select><button className="button secondary compact" disabled={!addPaperId} onClick={() => void addPaper()}><Plus size={14} /> 加入</button></div>
            <div className="project-paper-table">
              {(selected.papers ?? []).map((membership) => <article key={String(membership.id ?? membership.paper_id)} className="project-paper-link" tabIndex={0} onClick={() => onOpenPaper(membership.paper_id)} onKeyDown={(event) => { if (event.key === 'Enter') onOpenPaper(membership.paper_id) }}><div className="project-paper-main"><span>{membership.paper?.arxiv_id ?? membership.paper_id}</span><h3>{membership.paper?.title ?? '论文'}</h3><div>{membership.tags.map((tag) => <em key={tag}>{tag}</em>)}</div></div><div className="project-paper-state"><span className={`state-badge ${membership.reading_state}`}>{READING_STATE_OPTIONS.find(([value]) => value === membership.reading_state)?.[1]}</span><strong>P{membership.priority}</strong></div><div className="project-paper-actions"><button className="icon-button" title="编辑分类库内阅读信息" onClick={(event) => { event.stopPropagation(); setMembershipEdit({ ...membership }) }}><SettingsIcon size={15} /></button><button className="icon-button danger" title="从分类库移除" onClick={(event) => { event.stopPropagation(); void removePaper(membership) }}><X size={16} /></button></div></article>)}
              {!selected.papers?.length && <div className="empty-project-papers"><FileText size={23} /><strong>分类库中还没有论文</strong><p>{selected.kind === 'favorites' ? '在论文列表点击收藏后会自动出现在这里。' : '从上方搜索全部已初筛论文并加入。'}</p></div>}
            </div>
          </> : <div className="detail-placeholder"><NotebookTabs size={28} /><p>创建或选择一个分类库</p></div>}
        </section>
      </div>
      {(creating || editing) && <Modal title={editing ? '编辑分类库' : '新建分类库'} onClose={() => { setCreating(false); setEditing(false) }}><form className="modal-form" onSubmit={saveProject}><label className="field"><span>分类名称</span><input autoFocus required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：世界模型、待复现、综述素材" /></label><label className="field"><span>分类说明</span><textarea rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="说明这个分类收录什么论文" /></label><label className="field"><span>标识色</span><div className="color-options">{PROJECT_COLORS.map((color) => <button type="button" className={draft.color === color ? 'active' : ''} key={color} style={{ background: color }} onClick={() => setDraft({ ...draft, color })} aria-label={`选择颜色 ${color}`}>{draft.color === color && <Check size={14} />}</button>)}</div></label><div className="modal-actions"><button type="button" className="button secondary" onClick={() => { setCreating(false); setEditing(false) }}>取消</button><button className="button primary" disabled={saving || !draft.name.trim()}><Save size={15} /> {saving ? '保存中' : '保存分类'}</button></div></form></Modal>}
      {membershipEdit && <Modal title="编辑分类库内阅读信息" onClose={() => setMembershipEdit(null)}><div className="modal-form"><label className="field"><span>阅读状态</span><select value={membershipEdit.reading_state} onChange={(event) => setMembershipEdit({ ...membershipEdit, reading_state: event.target.value as ReadingState })}>{READING_STATE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="field"><span>分类内优先级</span><select value={membershipEdit.priority} onChange={(event) => setMembershipEdit({ ...membershipEdit, priority: Number(event.target.value) })}>{[5, 4, 3, 2, 1].map((value) => <option value={value} key={value}>{value} · {PRIORITY_LABELS[value]}</option>)}</select></label><TagEditor label="分类标签" tags={membershipEdit.tags} placeholder="输入标签" onChange={(tags) => setMembershipEdit({ ...membershipEdit, tags })} /><label className="field"><span>分类笔记</span><textarea rows={4} value={membershipEdit.notes} onChange={(event) => setMembershipEdit({ ...membershipEdit, notes: event.target.value })} /></label><div className="modal-actions"><button className="button secondary" onClick={() => setMembershipEdit(null)}>取消</button><button className="button primary" onClick={() => void saveMembership()}><Save size={15} /> 保存</button></div></div></Modal>}
    </div>
  )
}

const PROJECT_COLORS = ['#1f7a5a', '#4267a9', '#b5662d', '#8a4d78', '#80621f', '#4f6d67']

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return <div className="modal-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button></header>{children}</section></div>
}

function TrendsView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [trends, setTrends] = useState<TopicTrends | null>(null)
  const [roadmap, setRoadmap] = useState<ResearchRoadmap | null>(null)
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'trends' | 'roadmap'>('trends')
  const [selectedNode, setSelectedNode] = useState('')
  const [trendPeriod, setTrendPeriod] = useState<'daily' | 'weekly'>('daily')

  const load = useCallback(async () => {
    setState('loading'); setError('')
    try {
      const [trendData, roadmapData] = isDemo ? [sampleTrends, sampleRoadmap] : await Promise.all([api.topicTrends(), api.roadmap()])
      setTrends(trendData); setRoadmap(roadmapData); setSelectedNode(roadmapData.nodes[0]?.id ?? ''); setState('ready')
    } catch (loadError) { setState('error'); setError(loadError instanceof Error ? loadError.message : '研究方向数据加载失败') }
  }, [])
  useEffect(() => { void load() }, [load])
  if (state === 'loading') return <WorkspaceSkeleton />
  if (state === 'error' || !trends || !roadmap) return <PageError message={error} onRetry={load} />
  const trendData = trendPeriod === 'daily' ? trends.daily : trends.weekly
  const topicItems = trendData?.topics ?? trends.topics ?? []
  const selected = roadmap.nodes.find((node) => node.id === selectedNode) ?? roadmap.nodes[0]
  return (
    <div className="standard-page trends-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow-row"><TrendingUp size={14} /> 本地研究情报</div>
          <h1>研究方向趋势与路线</h1>
          <p>研究方向只来自“关注设置”中启用的正式研究方向；arXiv 分类、关键词、AI 主题、我的标签和分类标签都不会成为趋势或路线节点。</p>
        </div>
        <div className="segmented-control view-tabs">
          <button className={tab === 'trends' ? 'active' : ''} onClick={() => setTab('trends')}><TrendingUp size={14} /> 研究方向趋势</button>
          <button className={tab === 'roadmap' ? 'active' : ''} onClick={() => setTab('roadmap')}><Map size={14} /> 研究方向路线</button>
        </div>
      </section>
      {tab === 'trends'
        ? <TopicTrendPanel trends={trends} period={trendPeriod} topics={topicItems} onPeriodChange={setTrendPeriod} />
        : <RoadmapPanel roadmap={roadmap} selected={selected} onSelect={setSelectedNode} onToast={onToast} />}
    </div>
  )
}

function TopicTrendPanel({ trends, period, topics, onPeriodChange }: { trends: TopicTrends; period: 'daily' | 'weekly'; topics: TopicTrendItem[]; onPeriodChange: (period: 'daily' | 'weekly') => void }) {
  const periodData = period === 'daily' ? trends.daily : trends.weekly
  const periodLabel = period === 'daily' ? '今日' : '近 7 日'
  const previousLabel = period === 'daily' ? '昨日' : '前 7 日'
  const maxCount = Math.max(1, ...topics.flatMap((topic) => (topic.series ?? []).map((item) => item.count)), ...topics.flatMap((topic) => [topic.count, topic.previous_count]))
  const fastestRising = [...topics].filter((topic) => topic.change > 0).sort((a, b) => b.change - a.change)[0]
  const formalCount = periodData?.paper_count ?? 0
  const candidateCount = periodData?.candidate_paper_count
  return (
    <div className="trends-layout">
      <section className="trend-table">
        {trends.awaiting_daily_update && <div className="trend-pending"><Clock3 size={15} /><div><strong>今日数据尚未更新</strong><span>当前继续展示最近完整研究日 {trends.latest_complete_date ?? trends.as_of}；每日任务完成后会自动切换到 {trends.requested_as_of}。</span></div></div>}
        <header>
          <div><h2>{periodLabel}研究方向变化</h2><p>{periodData?.start_date ?? trends.as_of}{periodData?.end_date && periodData.end_date !== periodData.start_date ? ` 至 ${periodData.end_date}` : ''}</p></div>
          <div className="trend-period-control"><button className={period === 'daily' ? 'active' : ''} onClick={() => onPeriodChange('daily')}>日变化</button><button className={period === 'weekly' ? 'active' : ''} onClick={() => onPeriodChange('weekly')}>周变化</button></div>
          <span>{formalCount} 篇正式方向论文{candidateCount !== undefined ? ` · ${candidateCount} 篇候选` : ''}</span>
        </header>
        <div className="trend-rows">
          {topics.map((topic) => <article key={topic.id}>
            <div className="trend-topic"><strong>{topic.name}</strong><span>{topic.count} 篇 · 均分 {Math.round(topic.average_score ?? 0)}</span></div>
            <MiniSeries values={(topic.series?.length ? topic.series : [{ period: previousLabel, count: topic.previous_count }, { period: periodLabel, count: topic.count }])} max={maxCount} />
            <div className={`momentum ${topic.direction}`}><span>{topic.direction === 'new' ? '新出现' : topic.direction === 'rising' ? '上升' : topic.direction === 'falling' ? '回落' : '稳定'}</span><strong>{topic.change > 0 ? '+' : ''}{topic.change}</strong></div>
          </article>)}
        </div>
      </section>
      <aside className="trend-aside">
        <h2>{periodLabel}观察</h2>
        <div className="trend-callout rising"><TrendingUp size={17} /><div><strong>上升最快的研究方向</strong><span>{fastestRising?.name ?? '暂无'} · 增加 {fastestRising?.change ?? 0} 篇</span></div></div>
        <div className="trend-callout"><Network size={17} /><div><strong>注意力集中度</strong><span>前 3 个研究方向覆盖 {Math.round(topics.slice(0, 3).reduce((sum, topic) => sum + (topic.share_percent ?? 0), 0))}% 的正式方向论文</span></div></div>
        <p className="method-note">{period === 'daily' ? '今日与昨日' : '近 7 日与此前 7 日'}按唯一论文数对比，只统计能归入当前正式研究方向的论文；抓取候选仍保留在“今日论文”，但不进入趋势。结果只反映本地收录。</p>
      </aside>
    </div>
  )
}

function MiniSeries({ values, max }: { values: Array<{ period: string; count: number }>; max: number }) {
  return <div className="mini-series" aria-label={values.map((item) => `${item.period} ${item.count}篇`).join('，')}>{values.map((item) => <span className={item.count === 0 ? 'zero' : ''} key={item.period} title={`${item.period} · ${item.count} 篇`} style={{ height: `${item.count === 0 ? 2 : Math.min(100, Math.max(6, item.count / Math.max(1, max) * 100))}%` }} />)}</div>
}

function RoadmapPanel({ roadmap, selected, onSelect, onToast }: { roadmap: ResearchRoadmap; selected?: ResearchRoadmap['nodes'][number]; onSelect: (id: string) => void; onToast: (kind: Toast['kind'], message: string) => void }) {
  const clusters = roadmap.clusters?.length ? roadmap.clusters : inferClusters(roadmap)
  const relationBasis = (roadmap.relation_basis ?? '连线只表示两个关注研究方向在同一篇论文中共现，不代表引用、因果或方法继承关系。').replace(/主题/g, '研究方向')
  const candidateCount = roadmap.coverage?.candidate_paper_count
  return (
    <div className="roadmap-layout">
      <section className="roadmap-canvas">
        <header>
          <div><h2>研究方向共现图</h2><p>{roadmap.window?.days ?? 90} 天观察窗 · {roadmap.coverage?.paper_count ?? 0} 篇正式方向论文{candidateCount !== undefined ? ` / ${candidateCount} 篇候选` : ''}</p></div>
          <span><Network size={14} /> {roadmap.relations.length} 条连接</span>
        </header>
        <div className="roadmap-clusters">
          {clusters.map((cluster) => <div className="roadmap-cluster" key={cluster.id} style={{ '--cluster-color': cluster.color ?? '#4267a9' } as React.CSSProperties}>
            <div className="cluster-head"><span>{cluster.label}</span><small>{cluster.description}</small></div>
            <div>{cluster.node_ids.map((id) => {
              const node = roadmap.nodes.find((item) => item.id === id)
              if (!node) return null
              const links = roadmap.relations.filter((edge) => edge.source === id || edge.target === id).length
              return <button className={selected?.id === id ? 'active' : ''} key={id} onClick={() => onSelect(id)}><i className={node.status} /><strong>{node.label}</strong><span>{node.paper_count} 篇 · {links} 连接</span></button>
            })}</div>
          </div>)}
        </div>
        <p className="relation-basis">{relationBasis}</p>
      </section>
      <aside className="roadmap-inspector">
        {selected ? <>
          <div className="node-status"><span className={selected.status}>{selected.status === 'emerging' ? '新兴研究方向' : selected.status === 'active' ? '活跃研究方向' : '稳定研究方向'}</span><strong>{Math.round(selected.average_score ?? 0)} 分</strong></div>
          <h2>{selected.label}</h2>
          <p>{selected.paper_count} 篇论文，最早出现于 {selected.first_seen ? new Date(`${selected.first_seen}T12:00:00`).toLocaleDateString('zh-CN') : '本地观察期内'}。</p>
          <InfoTags label="归因关键词" values={selected.keywords ?? []} />
          <InfoTags label="常见方法" values={selected.methods ?? []} />
          <div className="connected-topics">
            <span>高关联研究方向</span>
            {roadmap.relations.filter((edge) => edge.source === selected.id || edge.target === selected.id).sort((a, b) => b.weight - a.weight).slice(0, 4).map((edge) => {
              const id = edge.source === selected.id ? edge.target : edge.source
              const node = roadmap.nodes.find((item) => item.id === id)
              return <button key={id} onClick={() => onSelect(id)}><span>{node?.label ?? id}</span><strong>{edge.weight} 篇共现</strong></button>
            })}
          </div>
          <button className="button secondary" onClick={() => { void navigator.clipboard.writeText(`${selected.label}: ${(selected.keywords ?? []).join('、')}`); onToast('success', '研究方向线索已复制') }}><Copy size={15} /> 复制研究方向线索</button>
        </> : <div className="detail-placeholder"><Map size={25} /><p>选择一个研究方向节点</p></div>}
      </aside>
    </div>
  )
}

function InfoTags({ label, values }: { label: string; values: string[] }) { return <div className="info-tags"><span>{label}</span><div>{values.length ? values.map((value) => <em key={value}>{value}</em>) : <small>暂无稳定信号</small>}</div></div> }

function inferClusters(roadmap: ResearchRoadmap) {
  const statusMeta = [{ id: 'emerging', label: '新兴研究方向', color: '#b5662d' }, { id: 'active', label: '活跃研究方向', color: '#1f7a5a' }, { id: 'established', label: '稳定研究方向', color: '#4267a9' }]
  return statusMeta.map((meta) => ({ ...meta, description: '', node_ids: roadmap.nodes.filter((node) => node.status === meta.id).map((node) => node.id) })).filter((cluster) => cluster.node_ids.length)
}

function ProfileView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [catalog, setCatalog] = useState<DirectionCatalog | null>(null)
  const [catalogQuery, setCatalogQuery] = useState('')
  const [catalogGroup, setCatalogGroup] = useState('全部')
  const [catalogLevel, setCatalogLevel] = useState<'all' | 'main' | 'specialty'>('all')
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setState('loading')
    try {
      const [nextProfile, nextCatalog] = isDemo
        ? [sampleProfile, demoDirectionCatalog(sampleProfile)]
        : await Promise.all([api.profile(), api.directionCatalog()])
      setProfile(nextProfile)
      setCatalog(nextCatalog)
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '研究画像加载失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const updateTopic = (index: number, patch: Partial<BackendTopic>) => {
    if (!profile) return
    setProfile({
      ...profile,
      topics: profile.topics.map((topic, topicIndex) => topicIndex === index ? { ...topic, ...patch } : topic),
    })
  }

  const addTopic = () => {
    if (!profile) return
    setProfile({
      ...profile,
      topics: [...profile.topics, {
        name: '新研究方向',
        query: '',
        arxiv_categories: ['cs.RO'],
        keywords: [],
        context_keywords: [],
        aliases: [],
        negative_keywords: [],
        weight: 1,
        enabled: true,
      }],
    })
  }

  const topicIdentities = useMemo(
    () => new Set((profile?.topics ?? []).flatMap((topic) => [topic.name, ...(topic.aliases ?? [])]).map(directionIdentity)),
    [profile],
  )
  const visibleCatalogItems = useMemo(() => {
    if (!catalog) return []
    const query = catalogQuery.normalize('NFKC').trim().toLocaleLowerCase()
    return catalog.items.filter((item) => {
      if (catalogGroup !== '全部' && item.group !== catalogGroup) return false
      if (catalogLevel !== 'all' && item.level !== catalogLevel) return false
      if (!query) return true
      return [item.name, item.group, item.parent_name ?? '', item.description, ...item.aliases, ...item.keywords]
        .join('\n').toLocaleLowerCase().includes(query)
    })
  }, [catalog, catalogGroup, catalogLevel, catalogQuery])

  const catalogItemExists = (item: DirectionCatalogItem) =>
    [item.name, ...item.aliases].some((label) => topicIdentities.has(directionIdentity(label)))

  const addCatalogTopic = (item: DirectionCatalogItem) => {
    if (!profile) return
    if (catalogItemExists(item)) {
      onToast('error', '该方向或它的同义名称已经存在，请在现有方向中维护')
      return
    }
    const { slug: _slug, group: _group, description: _description, level: _level, parent_slug: _parentSlug, parent_name: _parentName, ...topic } = item
    setProfile({ ...profile, topics: [...profile.topics, { ...topic, query: '' }] })
    onToast('success', `已加入“${item.name}”，保存后用于后续筛选`)
  }

  const removeTopic = (index: number) => {
    if (!profile) return
    setProfile({ ...profile, topics: profile.topics.filter((_, topicIndex) => topicIndex !== index) })
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!profile) return
    if (!profile.topics.length || profile.topics.some((topic) => !topic.name.trim())) {
      onToast('error', '至少保留一个有名称的研究方向')
      return
    }
    if (!profile.topics.some((topic) => topic.enabled !== false)) {
      onToast('error', '至少需要启用一个研究方向')
      return
    }
    const directionLabels = profile.topics.flatMap((topic) => [topic.name, ...(topic.aliases ?? [])])
    const directionKeys = directionLabels.map(directionIdentity)
    if (new Set(directionKeys).size !== directionKeys.length) {
      onToast('error', '正式名称和别名不能重复；相同含义请放在同一个方向下')
      return
    }
    setSaving(true)
    try {
      const saved = isDemo ? profile : await api.saveProfile(profile)
      setProfile(saved)
      onToast('success', '研究画像已保存，将用于下一次筛选')
    } catch (saveError) {
      onToast('error', saveError instanceof Error ? saveError.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (state === 'loading' || !profile) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />

  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SlidersHorizontal size={14} /> 个性化筛选</div><h1>关注设置</h1><p>日常只需填写研究方向和匹配词；精细规则按需展开。</p></div>
        <button className="button primary" type="submit" form="profile-form" disabled={saving} aria-label={saving ? '正在保存关注设置' : '保存关注设置'} title={saving ? '正在保存' : '保存更改'}><Save size={16} /><span>{saving ? '保存中' : '保存更改'}</span></button>
      </section>
      <form id="profile-form" className="form-layout" onSubmit={save}>
        <div className="form-main">
          {catalog && <FormSection title="AI 研究方向候选库" description={`目录版本 ${catalog.version}，包含 ${catalog.main_count} 个稳定主干和 ${catalog.specialty_count} 个精确专题。候选不会自动启用，只添加真正长期关注的方向。`}>
            <div className="direction-catalog-tools">
              <label className="search-box"><Search size={15} /><input value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="搜索方向、英文名、缩写或匹配词" />{catalogQuery && <button type="button" onClick={() => setCatalogQuery('')} title="清空搜索"><X size={14} /></button>}</label>
              <label className="catalog-group-select"><span>领域</span><select value={catalogGroup} onChange={(event) => setCatalogGroup(event.target.value)}><option>全部</option>{catalog.groups.map((group) => <option key={group}>{group}</option>)}</select></label>
              <label className="catalog-group-select"><span>粒度</span><select value={catalogLevel} onChange={(event) => setCatalogLevel(event.target.value as 'all' | 'main' | 'specialty')}><option value="all">全部</option><option value="main">主干</option><option value="specialty">专题</option></select></label>
            </div>
            <div className="catalog-summary"><span>显示 {visibleCatalogItems.length} 个方向</span><p>{catalog.scope_note}</p></div>
            <div className="direction-catalog-list">
              {visibleCatalogItems.map((item) => { const exists = catalogItemExists(item); return <article key={item.slug} className={exists ? 'exists' : ''}>
                <div className="catalog-item-main"><span>{item.group} · {item.level === 'specialty' ? `专题 · 隶属 ${item.parent_name ?? '主干方向'}` : '主干方向'}</span><h3>{item.name}</h3><p>{item.description}</p><div>{item.aliases.slice(0, 3).map((alias) => <em key={alias}>{alias}</em>)}</div></div>
                <div className="catalog-item-actions"><button type="button" className="button secondary" disabled={exists} onClick={() => addCatalogTopic(item)}>{exists ? <><Check size={14} /> 已添加</> : <><Plus size={14} /> 添加</>}</button></div>
                <details><summary><SlidersHorizontal size={13} /> 查看匹配规则 <ChevronDown size={13} /></summary><div><InfoTags label="匹配词" values={item.keywords} /><InfoTags label="领域限定" values={item.context_keywords} /><InfoTags label="排除词" values={item.negative_keywords} /><InfoTags label="arXiv 分类" values={item.arxiv_categories} /></div></details>
              </article> })}
              {!visibleCatalogItems.length && <div className="catalog-empty"><Search size={18} /><span>没有匹配的候选方向</span><small>尝试更短的中文名、英文名或缩写。</small></div>}
            </div>
            <footer className="catalog-footer"><CircleAlert size={14} /><span>建议最终启用 3–12 个边界明确的方向。选择过多会扩大抓取范围、削弱日报重点，并增加摘要初筛成本。</span></footer>
          </FormSection>}
          <FormSection title="我关注的研究方向" description="一个方向对应趋势和日报中的一个名称。相近概念放在同一方向的匹配词里，不必拆成多个标签。">
            <p className="direction-language-note">方向名称可以使用中文；别名、匹配词和领域限定词用于英文语义查询，排除词用于后续方向判断，这些内容都请使用英文。</p>
            <div className="direction-editor-list">
              {profile.topics.map((topic, index) => <article className={`direction-editor ${topic.enabled === false ? 'disabled' : ''}`} key={topic.id ?? `new-${index}`}>
                <header>
                  <label className="field"><span>方向名称</span><input value={topic.name} onChange={(event) => updateTopic(index, { name: event.target.value })} placeholder="例如：世界模型与规划" /></label>
                  <div className="direction-editor-actions">
                    <button type="button" className="icon-button" title={topic.enabled === false ? '启用方向' : '停用方向'} onClick={() => updateTopic(index, { enabled: topic.enabled === false })}>{topic.enabled === false ? <ToggleLeft size={20} /> : <ToggleRight size={20} />}</button>
                    <button type="button" className="icon-button danger" title="删除方向" onClick={() => removeTopic(index)}><Trash2 size={16} /></button>
                  </div>
                </header>
                <TagEditor label="匹配词" tone="positive" tags={topic.keywords ?? []} placeholder="例如 world model、contact-rich manipulation" onChange={(keywords) => updateTopic(index, { keywords })} />
                <details className="direction-advanced">
                  <summary><SlidersHorizontal size={14} /><span>高级规则</span><small>别名、误匹配控制与抓取范围</small><ChevronDown size={14} /></summary>
                  <div>
                    <TagEditor label="方向别名（名称归并与语义查询）" tags={topic.aliases ?? []} placeholder="例如 VLA、Vision-Language-Action" onChange={(aliases) => updateTopic(index, { aliases })} />
                    <TagEditor label="领域限定词（至少命中一个）" tags={topic.context_keywords ?? []} placeholder="例如 robot、humanoid；留空则不限制" onChange={(context_keywords) => updateTopic(index, { context_keywords })} />
                    <TagEditor label="排除词" tone="negative" tags={topic.negative_keywords ?? []} placeholder="输入容易造成误匹配的词" onChange={(negative_keywords) => updateTopic(index, { negative_keywords })} />
                    <div className="direction-editor-meta">
                      <label className="field"><span>方向权重</span><input type="number" min="0" max="5" step="0.05" value={topic.weight ?? 1} onChange={(event) => updateTopic(index, { weight: Number(event.target.value) })} /></label>
                      <small>arXiv 分类只扩大抓取范围，不能独立证明论文属于这个研究方向。</small>
                    </div>
                    <CategoryPicker selected={topic.arxiv_categories ?? []} onChange={(arxiv_categories) => updateTopic(index, { arxiv_categories })} />
                  </div>
                </details>
              </article>)}
              <button className="button secondary direction-add" type="button" onClick={addTopic}><Plus size={15} /> 新建研究方向</button>
            </div>
          </FormSection>
          <details className="form-section optional-section">
            <summary><div><h2>个性化描述与作者关注</h2><p>可选；用于给 AI 更多研究背景，并提高关注作者的论文优先级。</p></div><ChevronDown size={16} /></summary>
            <div className="form-section-body">
              <label className="field"><span>画像名称</span><input value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.target.value })} /></label>
              <label className="field"><span>研究兴趣描述</span><textarea rows={5} value={profile.description} onChange={(event) => setProfile({ ...profile, description: event.target.value })} placeholder="例如：关注具身智能中的世界模型与接触丰富操作……" /><small>这段描述会作为论文评估上下文提供给模型。</small></label>
              <TagEditor label="关注作者" tags={profile.author_watchlist} placeholder="输入作者英文名" onChange={(tags) => setProfile({ ...profile, author_watchlist: tags })} />
            </div>
          </details>
        </div>
        <aside className="form-aside">
          <div className="sticky-panel">
            <h2>设置原则</h2>
            <ul className="plain-check-list"><li>方向名称保持少而稳定</li><li>相近叫法放进同一方向</li><li>先填匹配词，误报多时再开高级规则</li><li>初筛和深读门槛在系统设置中调整</li></ul>
          </div>
        </aside>
      </form>
    </div>
  )
}

function directionIdentity(value: string) {
  return value.normalize('NFKC').trim().replace(/[\s_-]+/g, '').toLocaleLowerCase()
}

function demoDirectionCatalog(profile: Profile): DirectionCatalog {
  const groups = ['示例方向']
  const items: DirectionCatalogItem[] = profile.topics.map((topic, index) => ({
    ...topic,
    slug: `demo-${index}`,
    group: groups[0],
    level: 'main',
    description: '演示模式中的现有研究方向。连接本地服务后可浏览完整 AI 方向库。',
  }))
  return { version: 'demo', updated_at: '', groups, count: items.length, items, sources: [], scope_note: '演示模式仅展示当前画像中的方向。' }
}

function SettingsView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [smtpPassword, setSmtpPassword] = useState('')
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [methodCatalog, setMethodCatalog] = useState<MethodCatalog | null>(null)
  const [promptCatalog, setPromptCatalog] = useState<AnalysisPromptCatalog | null>(null)
  const [methodCandidates, setMethodCandidates] = useState<MethodCandidate[]>([])
  const [candidateNames, setCandidateNames] = useState<Record<string, string>>({})
  const [methodBusy, setMethodBusy] = useState(false)
  const [embeddingTesting, setEmbeddingTesting] = useState(false)
  const [emailTesting, setEmailTesting] = useState(false)

  const load = useCallback(async () => {
    setState('loading')
    try {
      if (isDemo) {
        setSettings(sampleSettings)
        setPromptCatalog(samplePromptCatalog)
      } else {
        const [loadedSettings, catalog, candidates, prompts] = await Promise.all([
          api.settings(), api.methodCatalog(), api.methodCandidates(), api.promptCatalog(),
        ])
        setSettings(loadedSettings)
        setMethodCatalog(catalog)
        setPromptCatalog(prompts)
        setMethodCandidates(candidates.items)
        setCandidateNames(Object.fromEntries(candidates.items.map((item) => [item.key, item.name])))
      }
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '系统设置加载失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!settings) return
    setSaving(true)
    try {
      const saved = isDemo ? { ...settings, api_key_configured: Boolean(apiKey) || settings.api_key_configured, smtp_password_configured: Boolean(smtpPassword) || settings.smtp_password_configured } : await api.saveSettings({ ...settings, ...(apiKey ? { api_key: apiKey } : {}), ...(smtpPassword ? { smtp_password: smtpPassword } : {}) })
      setSettings(saved)
      setApiKey('')
      setSmtpPassword('')
      onToast('success', '系统设置已保存')
    } catch (saveError) {
      onToast('error', saveError instanceof Error ? saveError.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    if (!settings) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = isDemo ? { ok: true, message: '连接成功，模型响应正常（演示）' } : await api.testConnection({ provider_name: settings.provider_name, base_url: settings.base_url, model: settings.model, ...(apiKey ? { api_key: apiKey } : {}) })
      setTestResult(result)
    } catch (testError) {
      setTestResult({ ok: false, message: testError instanceof Error ? testError.message : '连接测试失败' })
    } finally {
      setTesting(false)
    }
  }

  const testEmbedding = async () => {
    if (!settings) return
    setEmbeddingTesting(true)
    try {
      const result = isDemo ? { ...settings.embedding, ok: true, loaded: true, available: true } : await api.testEmbedding()
      setSettings({ ...settings, embedding: result })
      onToast(result.ok ? 'success' : 'error', result.ok ? '语义模型已下载并完成预热' : (result.message ?? '语义模型不可用'))
    } catch (embeddingError) {
      onToast('error', embeddingError instanceof Error ? embeddingError.message : '语义模型预热失败')
    } finally { setEmbeddingTesting(false) }
  }

  const testEmail = async () => {
    if (!settings) return
    setEmailTesting(true)
    try {
      if (!isDemo) {
        const saved = await api.saveSettings({ ...settings, ...(smtpPassword ? { smtp_password: smtpPassword } : {}) })
        setSettings(saved); setSmtpPassword('')
        const result = await api.testEmail()
        if (!result.ok) throw new Error(`测试邮件发送失败（${result.email_failed} 个收件人）`)
        onToast('success', `测试邮件已发送给 ${result.email_sent} 个收件人`)
      } else onToast('success', '演示模式不发送真实邮件')
    } catch (emailError) {
      onToast('error', emailError instanceof Error ? emailError.message : '测试邮件发送失败')
    } finally { setEmailTesting(false) }
  }

  if (state === 'loading' || !settings) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />
  const insecure = settings.base_url.trim().toLowerCase().startsWith('http://')
  const budgetOptions = [
    { id: 'economy' as const, name: '经济', detail: '正文 1.6 万字符 / PDF 12 页；初筛 900、深读 2,000 输出 Token' },
    { id: 'balanced' as const, name: '平衡', detail: '正文 3 万字符 / PDF 25 页；初筛 1,200、深读 3,500 输出 Token' },
    { id: 'deep' as const, name: '深度', detail: '正文 4.5 万字符 / PDF 40 页；初筛 1,600、深读 5,000 输出 Token' },
  ]
  const recallOptions = [
    { id: 'high' as const, name: '高召回', detail: '语义相似度 0.66；优先减少漏检' },
    { id: 'balanced' as const, name: '平衡', detail: '语义相似度 0.68；兼顾覆盖与成本' },
    { id: 'precise' as const, name: '精准', detail: '语义相似度 0.70；减少边缘候选' },
  ]
  const selectedPromptTemplate = promptCatalog?.templates.find((item) => item.id === settings.analysis_prompt_template_id)
  const promptModified = Boolean(selectedPromptTemplate && (
    settings.screening_prompt !== selectedPromptTemplate.screening_prompt
    || settings.deep_prompt !== selectedPromptTemplate.deep_prompt
  ))
  const applyPromptTemplate = (templateId: string) => {
    const template = promptCatalog?.templates.find((item) => item.id === templateId)
    if (!template) return
    setSettings({
      ...settings,
      analysis_prompt_template_id: template.id,
      screening_prompt: template.screening_prompt,
      deep_prompt: template.deep_prompt,
      analysis_prompt_customized: false,
    })
  }
  const acceptCandidate = async (candidate: MethodCandidate) => {
    if (isDemo) return
    setMethodBusy(true)
    try {
      const catalog = await api.acceptMethodCandidate(candidate.key, candidateNames[candidate.key]?.trim() || candidate.name)
      setMethodCatalog(catalog)
      setMethodCandidates((items) => items.filter((item) => item.key !== candidate.key))
      onToast('success', `已接受方法：${candidate.name}`)
    } catch (acceptError) {
      onToast('error', acceptError instanceof Error ? acceptError.message : '接受候选失败')
    } finally { setMethodBusy(false) }
  }
  const dismissCandidate = async (candidate: MethodCandidate) => {
    if (isDemo) return
    setMethodBusy(true)
    try {
      await api.dismissMethodCandidate(candidate.key)
      setMethodCandidates((items) => items.filter((item) => item.key !== candidate.key))
    } catch (dismissError) {
      onToast('error', dismissError instanceof Error ? dismissError.message : '忽略候选失败')
    } finally { setMethodBusy(false) }
  }
  const removeCustomMethod = async (name: string) => {
    if (!methodCatalog || isDemo) return
    setMethodBusy(true)
    try {
      const retained = methodCatalog.items.filter((item) => item.source === 'custom' && item.name !== name).map((item) => ({ name: item.name, aliases: item.aliases.filter((alias) => alias !== item.name) }))
      setMethodCatalog(await api.saveCustomMethods(retained))
    } catch (removeError) {
      onToast('error', removeError instanceof Error ? removeError.message : '删除自定义方法失败')
    } finally { setMethodBusy(false) }
  }

  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SettingsIcon size={14} /> 本地服务配置</div><h1>系统设置</h1><p>配置模型 API、报告时区和分析规模。</p></div>
        <button className="button primary" type="submit" form="settings-form" disabled={saving} aria-label={saving ? '正在保存系统设置' : '保存系统设置'} title={saving ? '正在保存' : '保存更改'}><Save size={16} /><span>{saving ? '保存中' : '保存更改'}</span></button>
      </section>

      <form id="settings-form" className="form-layout" onSubmit={save}>
        <div className="form-main">
          <FormSection title="模型 API" description="支持 OpenAI 兼容接口。密钥仅由本地后端读取和保存。">
            <div className="field-grid two">
              <label className="field"><span>服务商</span><select value={settings.provider_name} onChange={(event) => setSettings({ ...settings, provider_name: event.target.value })}><option value="codex">Codex 本机配置</option><option value="openai-compatible">OpenAI 兼容</option><option value="openai">OpenAI</option><option value="custom">自定义</option></select></label>
              <label className="field"><span>模型</span><input value={settings.model} onChange={(event) => setSettings({ ...settings, model: event.target.value })} placeholder="gpt-5-mini" /></label>
            </div>
            <label className="field"><span>Base URL</span><input value={settings.base_url} onChange={(event) => { setSettings({ ...settings, base_url: event.target.value }); setTestResult(null) }} placeholder="https://api.openai.com/v1" /></label>
            {insecure && <div className="warning-banner"><ShieldAlert size={18} /><div><strong>连接未加密</strong><span>这个地址使用 HTTP，API 密钥可能在网络中明文传输。除本机调试外请使用 HTTPS。</span></div></div>}
            <label className="field"><span>API Key</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" placeholder={settings.api_key_configured ? '已配置，留空则不修改' : '输入 API Key'} />{settings.api_key_configured && !apiKey && <span className="configured-label"><Check size={13} /> 已配置</span>}</div><small>不会返回到浏览器；保存后界面只显示配置状态。</small></label>
            <div className="connection-row">
              <button type="button" className="button secondary" onClick={test} disabled={testing || !settings.base_url || !settings.model}><Wifi size={16} /> {testing ? '正在测试' : '测试连接'}</button>
              {testResult && <span className={`connection-result ${testResult.ok ? 'ok' : 'failed'}`}>{testResult.ok ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}{testResult.message}</span>}
            </div>
          </FormSection>

          <FormSection title="报告时间偏好" description="这里保存应用内的日期与时间显示偏好，不会修改 Windows 计划任务。真实自动运行时间请运行 install_task.ps1 更新。">
            <div className="field-grid two">
              <label className="field"><span>记录的计划时间</span><input type="time" value={settings.schedule_time} onChange={(event) => setSettings({ ...settings, schedule_time: event.target.value })} /></label>
              <label className="field"><span>时区</span><select value={settings.timezone} onChange={(event) => setSettings({ ...settings, timezone: event.target.value })}><option value="Asia/Shanghai">Asia/Shanghai</option><option value="UTC">UTC</option><option value="America/New_York">America/New_York</option><option value="Europe/London">Europe/London</option></select></label>
            </div>
          </FormSection>

          <FormSection title="日报邮件" description="完整日更保存日报后自动发送态势摘要和前 10 篇重点论文；授权码只保存在后端。">
            <label className="toggle-field"><input type="checkbox" checked={settings.email_enabled} onChange={(event) => setSettings({ ...settings, email_enabled: event.target.checked })} /><span><strong>自动发送日报邮件</strong><small>初筛未完成时邮件会明确标记失败数量</small></span></label>
            <div className="field-grid two"><label className="field"><span>SMTP 主机</span><input value={settings.smtp_host} onChange={(event) => setSettings({ ...settings, smtp_host: event.target.value })} placeholder="smtp.example.com" /></label><label className="field"><span>端口与安全模式</span><div className="smtp-port-row"><input type="number" min="1" max="65535" value={settings.smtp_port} onChange={(event) => setSettings({ ...settings, smtp_port: Number(event.target.value) })} /><select value={settings.smtp_security} onChange={(event) => setSettings({ ...settings, smtp_security: event.target.value as Settings['smtp_security'] })}><option value="starttls">STARTTLS</option><option value="ssl">SSL</option></select></div></label></div>
            <div className="field-grid two"><label className="field"><span>SMTP 用户名</span><input value={settings.smtp_username} onChange={(event) => setSettings({ ...settings, smtp_username: event.target.value })} autoComplete="username" /></label><label className="field"><span>授权码</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={smtpPassword} onChange={(event) => setSmtpPassword(event.target.value)} autoComplete="new-password" placeholder={settings.smtp_password_configured ? '已配置，留空则不修改' : '输入 SMTP 授权码'} />{settings.smtp_password_configured && !smtpPassword && <span className="configured-label"><Check size={13} /> 已配置</span>}</div></label></div>
            <div className="field-grid two"><label className="field"><span>发件人</span><input type="email" value={settings.smtp_from} onChange={(event) => setSettings({ ...settings, smtp_from: event.target.value })} placeholder="radar@example.com" /></label><label className="field"><span>收件人</span><input value={settings.email_recipients.join(', ')} onChange={(event) => setSettings({ ...settings, email_recipients: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} placeholder="you@example.com, team@example.com" /></label></div>
            <label className="field"><span>公网基础地址</span><input value={settings.public_base_url} onChange={(event) => setSettings({ ...settings, public_base_url: event.target.value })} placeholder="https://203.0.113.10" /><small>邮件中的完整日报和论文详情链接使用此同源地址。</small></label>
            <button type="button" className="button secondary" disabled={emailTesting || !settings.smtp_host || !settings.smtp_from || !settings.email_recipients.length} onClick={() => void testEmail()}><Mail size={16} />{emailTesting ? '正在发送' : '保存并发送测试邮件'}</button>
          </FormSection>

          <FormSection title="处理规模与门槛" description="每次日更先用本地语义模型召回，再把所有候选分批完成摘要初筛，最后限量全文深读。">
            <div className="embedding-status"><div><Network size={18} /><span><strong>本地语义模型</strong><small>{settings.embedding.model} · {settings.embedding.dimensions} 维 · {settings.embedding.loaded ? '已加载' : settings.embedding.available ? '等待首次加载' : '尚未下载'}{settings.embedding.backfill?.status === 'running' ? ` · 历史向量 ${settings.embedding.backfill.processed ?? 0}/${settings.embedding.backfill.total ?? 0}` : settings.embedding.backfill?.status === 'completed' ? ` · 历史向量已补齐 ${settings.embedding.backfill.total ?? 0} 篇` : ''}</small></span></div><button type="button" className="button secondary compact" disabled={embeddingTesting} onClick={() => void testEmbedding()}><RefreshCw className={embeddingTesting ? 'spin' : ''} size={14} />{embeddingTesting ? '预热中' : '下载并预热'}</button></div>
            <div className="budget-mode-list" role="radiogroup" aria-label="语义召回档位">
              {recallOptions.map((option) => <label key={option.id} className={settings.semantic_recall_mode === option.id ? 'selected' : ''}><input type="radio" name="semantic-recall" checked={settings.semantic_recall_mode === option.id} onChange={() => setSettings({ ...settings, semantic_recall_mode: option.id })} /><span><strong>{option.name}</strong><small>{option.detail}</small></span></label>)}
            </div>
            <div className="budget-mode-list" role="radiogroup" aria-label="单篇分析 Token 档位">
              {budgetOptions.map((option) => <label key={option.id} className={settings.analysis_budget_mode === option.id ? 'selected' : ''}><input type="radio" name="analysis-budget" checked={settings.analysis_budget_mode === option.id} onChange={() => setSettings({ ...settings, analysis_budget_mode: option.id })} /><span><strong>{option.name}</strong><small>{option.detail}</small></span></label>)}
            </div>
            <label className="toggle-field"><input type="checkbox" checked={settings.daily_ai_synthesis_enabled} onChange={(event) => setSettings({ ...settings, daily_ai_synthesis_enabled: event.target.checked })} /><span><strong>日更后自动生成 AI 综合研判</strong><small>每天只对最终日报调用一次；经济 / 平衡 / 深度档的最大输出约为 2,000 / 3,500 / 5,000 Token，打开日报不会重复调用。</small></span></label>
            <label className="field compact-number-field"><span>本轮最多抓取的候选论文</span><input type="number" min="10" max="1000" value={settings.max_results} onChange={(event) => setSettings({ ...settings, max_results: Number(event.target.value) })} /></label>
            <div className="analysis-thresholds">
              <article>
                <header><span>第 1 级</span><h3>摘要初筛</h3></header>
                <p>语义相似度决定普通候选，关注作者可直接进入候选；方向词用于构造英文语义查询，不是硬命中门槛。</p>
                <label className="field"><span>单批初筛大小</span><div><input type="number" min="1" max={settings.max_results} value={settings.screen_batch_size} onChange={(event) => setSettings({ ...settings, screen_batch_size: Number(event.target.value) })} /><em>篇</em></div><small>一次日更自动连续处理全部批次，每批内部最多 4 篇并发。</small></label>
              </article>
              <article>
                <header><span>第 2 级</span><h3>全文深读</h3></header>
                <p>摘要初筛完成后，AI 相关度达到 <strong>{settings.deep_relevance_threshold ?? 90}</strong> 分才进入全文队列。</p>
                <label className="field"><span>每轮最多深读</span><div><input type="number" min="0" max="100" value={settings.deep_analysis_top_n} onChange={(event) => setSettings({ ...settings, deep_analysis_top_n: Number(event.target.value) })} /><em>篇</em></div></label>
              </article>
            </div>
            <label className="field"><span>全文深读的 AI 相关度门槛</span><div className="range-readout"><span>越高越省 Token，但可能漏掉边缘相关论文</span><strong>{settings.deep_relevance_threshold ?? 90}</strong></div><input className="range" type="range" min="0" max="100" step="5" value={settings.deep_relevance_threshold ?? 90} onChange={(event) => setSettings({ ...settings, deep_relevance_threshold: Number(event.target.value) })} /></label>
            <div className="cost-note"><Sparkles size={16} /><span>本地向量与词法召回不消耗模型 Token；所有召回候选都会分批调用摘要初筛，全文深读每次最多 {settings.deep_analysis_top_n} 篇。候选越多，摘要初筛 Token 用量越高。</span></div>
          </FormSection>

          <FormSection title="论文分析提示词" description="模板与编辑内容从下一次摘要初筛和全文深读开始生效，不会重写已有分析。">
            {promptCatalog && <>
              <div className="prompt-template-toolbar">
                <label className="field"><span>推荐模板</span><select value={settings.analysis_prompt_template_id} onChange={(event) => applyPromptTemplate(event.target.value)}>{promptCatalog.templates.map((template) => <option value={template.id} key={template.id}>{template.name}</option>)}</select></label>
                <div className="prompt-template-summary"><span className={promptModified ? 'modified' : ''}>{promptModified ? '已修改' : '模板原文'}</span><strong>{selectedPromptTemplate?.name}</strong><p>{selectedPromptTemplate?.description}</p><small>目录版本 {promptCatalog.version}</small></div>
                <button type="button" className="button secondary compact" disabled={!promptModified} onClick={() => applyPromptTemplate(settings.analysis_prompt_template_id)}><RotateCcw size={14} /> 恢复模板</button>
              </div>
              <div className="prompt-editor-grid">
                <label className="field prompt-editor"><span className="prompt-field-head"><strong>摘要初筛侧重</strong><small>{settings.screening_prompt.length} / {promptCatalog.max_prompt_chars}</small></span><textarea rows={7} maxLength={promptCatalog.max_prompt_chars} value={settings.screening_prompt} onChange={(event) => setSettings({ ...settings, screening_prompt: event.target.value, analysis_prompt_customized: true })} /></label>
                <label className="field prompt-editor"><span className="prompt-field-head"><strong>全文深读侧重</strong><small>{settings.deep_prompt.length} / {promptCatalog.max_prompt_chars}</small></span><textarea rows={9} maxLength={promptCatalog.max_prompt_chars} value={settings.deep_prompt} onChange={(event) => setSettings({ ...settings, deep_prompt: event.target.value, analysis_prompt_customized: true })} /></label>
              </div>
              <details className="fixed-prompt-details"><summary><ShieldAlert size={15} /><span>查看不可编辑的固定系统规则</span><ChevronDown size={14} /></summary><div><section><strong>摘要初筛</strong><pre>{promptCatalog.fixed_prompts.screening}</pre></section><section><strong>全文深读</strong><pre>{promptCatalog.fixed_prompts.deep}</pre></section></div></details>
            </>}
          </FormSection>

          <FormSection title="Token 用量" description="只统计模型服务商在响应中返回的真实 usage；没有 usage 的旧记录或兼容服务不会被估算成 Token。">
            <div className="token-usage-grid">
              <div><span>累计总 Token</span><strong>{formatInteger(settings.token_usage.total_tokens)}</strong></div>
              <div><span>输入</span><strong>{formatInteger(settings.token_usage.input_tokens)}</strong></div>
              <div><span>缓存输入</span><strong>{formatInteger(settings.token_usage.cached_input_tokens)}</strong></div>
              <div><span>输出 / 推理</span><strong>{formatInteger(settings.token_usage.output_tokens)} / {formatInteger(settings.token_usage.reasoning_tokens)}</strong></div>
            </div>
            <div className="token-kind-breakdown">
              <span>摘要初筛 <strong>{formatInteger(settings.token_usage.by_kind.screening?.total_tokens ?? 0)}</strong></span>
              <span>全文深读 <strong>{formatInteger(settings.token_usage.by_kind.deep?.total_tokens ?? 0)}</strong></span>
              <span>日报综合研判 <strong>{formatInteger(settings.token_usage.by_kind.report_synthesis?.total_tokens ?? 0)}</strong></span>
            </div>
            {settings.token_usage.latest ? <div className="latest-token-row"><span>最近一次：{settings.token_usage.latest.kind === 'deep' ? '全文深读' : settings.token_usage.latest.kind === 'report_synthesis' ? '日报综合研判' : '摘要初筛'} · {settings.token_usage.latest.budget_mode}</span><strong>{formatInteger(settings.token_usage.latest.total_tokens ?? 0)} Token</strong><small>{formatDateTime(settings.token_usage.latest.created_at)}</small></div> : <div className="retention-note"><CircleAlert size={16} /><div><strong>还没有可计量的 AI 调用</strong><p>下一次真实分析且 Provider 返回 usage 后，这里会开始累计。</p></div></div>}
            {settings.token_usage.usage_missing_count > 0 && <small className="usage-missing">另有 {settings.token_usage.usage_missing_count} 份历史分析未提供 usage，不计为 0。</small>}
          </FormSection>

          <FormSection title="共同方法词表" description="内置词表覆盖主要 AI 方法族；候选从最近 30 天已保存的分析中本地发现，至少跨 2 篇论文重复，接受前不会出现在日报。">
            {methodCatalog && <div className="method-catalog-summary"><div><strong>{methodCatalog.built_in_count}</strong><span>内置方法</span></div><div><strong>{methodCatalog.groups.length}</strong><span>稳定方法族</span></div><div><strong>{methodCatalog.custom_count}</strong><span>已接受候选</span></div><small>词表版本 {methodCatalog.version}</small></div>}
            {methodCatalog && methodCatalog.items.some((item) => item.source === 'custom') && <div className="custom-methods"><span>自定义方法</span><div>{methodCatalog.items.filter((item) => item.source === 'custom').map((item) => <span key={item.slug}>{item.name}<button type="button" disabled={methodBusy} onClick={() => void removeCustomMethod(item.name)} aria-label={`删除 ${item.name}`} title="删除自定义方法"><X size={12} /></button></span>)}</div></div>}
            <div className="method-candidates-head"><div><strong>待审核候选</strong><small>候选发现不调用模型，Token 成本为 0</small></div><span>{methodCandidates.length}</span></div>
            {methodCandidates.length ? <div className="method-candidate-list">{methodCandidates.map((candidate) => <article key={candidate.key}><div><input className="candidate-name-input" value={candidateNames[candidate.key] ?? candidate.name} onChange={(event) => setCandidateNames((values) => ({ ...values, [candidate.key]: event.target.value }))} aria-label={`${candidate.name} 的规范名称`} /><small>{candidate.paper_count} 篇论文重复出现 · 原始短语：{candidate.name}</small><p>{candidate.representative_papers.map((paper) => paper.title).join('；')}</p></div><div><button type="button" className="icon-button" disabled={methodBusy} onClick={() => void dismissCandidate(candidate)} title="忽略候选" aria-label={`忽略 ${candidate.name}`}><X size={15} /></button><button type="button" className="button secondary" disabled={methodBusy || !(candidateNames[candidate.key] ?? candidate.name).trim()} onClick={() => void acceptCandidate(candidate)}><Check size={15} /> 接受</button></div></article>)}</div> : <div className="method-empty"><CheckCircle2 size={18} /><span>当前没有达到重复门槛的新候选</span></div>}
          </FormSection>

          <FormSection title="本地数据" description="论文、分析、反馈与每日日报都保存在这台电脑上。">
            <div className="database-status">
              <div><Database size={20} /><span><strong>{formatBytes(settings.database_size_bytes ?? 0)}</strong><small>当前数据库大小</small></span></div>
              <div><strong>{settings.stored_paper_count}</strong><small>篇论文</small></div>
              <div><strong>{settings.stored_analysis_count}</strong><small>份 AI 分析</small></div>
              <div><strong>{settings.stored_report_count}</strong><small>份日报快照</small></div>
            </div>
            <div className="retention-note"><CircleAlert size={16} /><div><strong>当前永久累积保存，不会自动清理</strong><p>“保留 {settings.retention_days} 天”目前只是预留配置，尚未启用清理。系统不长期保存 PDF、arXiv HTML 正文或模型读取的临时全文。</p></div></div>
            {settings.database_path && <label className="field"><span>数据库位置</span><input value={settings.database_path} readOnly /></label>}
          </FormSection>
        </div>
        <aside className="form-aside">
          <div className="sticky-panel security-panel">
            <h2><KeyRound size={17} /> 密钥安全</h2>
            <ul><li>API Key 不写入前端代码</li><li>浏览器无法读取已保存密钥</li><li>建议使用限额独立密钥</li><li>本地服务不要暴露到公网</li></ul>
          </div>
        </aside>
      </form>
    </div>
  )
}

function FormSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="form-section"><div className="form-section-head"><h2>{title}</h2><p>{description}</p></div><div className="form-section-body">{children}</div></section>
}

function TagEditor({ label, tags, onChange, placeholder, tone = 'neutral' }: { label: string; tags: string[]; onChange: (tags: string[]) => void; placeholder: string; tone?: 'neutral' | 'positive' | 'negative' }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const next = draft.trim().replace(/,$/, '')
    const key = next.toLocaleLowerCase().replace(/[\s_-]+/g, '')
    if (next && !tags.some((tag) => tag.toLocaleLowerCase().replace(/[\s_-]+/g, '') === key)) onChange([...tags, next])
    setDraft('')
  }
  return (
    <div className="field tag-field"><span>{label}</span><div className={`tag-editor ${tone}`}>
      {tags.map((tag) => <span className="editable-tag" key={tag}>{tag}<button type="button" onClick={() => onChange(tags.filter((item) => item !== tag))} aria-label={`移除 ${tag}`}><X size={13} /></button></span>)}
      <input value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={add} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add() } if (event.key === 'Backspace' && !draft && tags.length) onChange(tags.slice(0, -1)) }} placeholder={tags.length ? '' : placeholder} />
      <button className="tag-add" type="button" onMouseDown={(event) => event.preventDefault()} onClick={add} aria-label="添加"><Plus size={15} /></button>
    </div></div>
  )
}

const ARXIV_CATEGORIES = [
  ['cs.RO', '机器人学'], ['cs.AI', '人工智能'], ['cs.LG', '机器学习'], ['cs.CV', '计算机视觉'],
  ['cs.CL', '计算与语言'], ['cs.IR', '信息检索'], ['cs.MA', '多智能体系统'], ['cs.HC', '人机交互'],
  ['cs.CR', '密码与安全'], ['cs.CY', '计算机与社会'], ['cs.SD', '声音'], ['cs.GR', '图形学'],
  ['cs.LO', '逻辑'], ['cs.DC', '分布式计算'], ['cs.PF', '性能'], ['cs.DB', '数据库'],
  ['cs.SI', '社会与信息网络'], ['cs.NE', '神经与演化计算'], ['cs.ET', '新兴技术'],
  ['stat.ML', '统计机器学习'], ['stat.ME', '统计方法'], ['math.OC', '优化与控制'],
  ['eess.SY', '系统与控制'], ['eess.AS', '音频语音'], ['eess.SP', '信号处理'],
  ['eess.IV', '图像视频处理'], ['q-bio.BM', '生物分子'], ['q-bio.QM', '定量方法'],
  ['physics.comp-ph', '计算物理'], ['physics.ao-ph', '大气海洋物理'], ['physics.chem-ph', '化学物理'],
  ['cond-mat.mtrl-sci', '材料科学'], ['quant-ph', '量子物理'], ['econ.EM', '计量经济学'],
]

function CategoryPicker({ selected, onChange }: { selected: string[]; onChange: (categories: string[]) => void }) {
  const toggle = (category: string) => onChange(selected.includes(category) ? selected.filter((item) => item !== category) : [...selected, category])
  return <div className="category-picker">{ARXIV_CATEGORIES.map(([code, name]) => <label key={code} className={selected.includes(code) ? 'selected' : ''}><input type="checkbox" checked={selected.includes(code)} onChange={() => toggle(code)} /><span className="check-box">{selected.includes(code) && <Check size={13} />}</span><strong>{code}</strong><small>{name}</small></label>)}</div>
}

function WorkspaceSkeleton() {
  return <div className="workspace-page loading-page"><div className="skeleton heading-skeleton" /><div className="skeleton metric-skeleton" /><div className="skeleton content-skeleton" /></div>
}

function PageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="page-state"><div className="state-icon error"><CircleAlert size={24} /></div><h1>暂时无法加载数据</h1><p>{message}</p><button className="button primary" onClick={onRetry}><RefreshCw size={16} /> 重新连接</button><small>请确认后端服务已在本机启动，并检查 `/api` 代理配置。</small></div>
}

function EmptyPapers({ filtered }: { filtered: boolean }) {
  return <div className="empty-state"><Inbox size={25} /><h3>{filtered ? '没有匹配的论文' : '今天还没有候选论文'}</h3><p>{filtered ? '调整关键词或筛选条件后再试。' : '可手动抓取最新论文，或等待下次定时任务。'}</p></div>
}

function getReading(paper: Paper): ReadingData {
  return {
    state: paper.reading?.state ?? paper.reading_state ?? 'unread',
    priority: paper.reading?.priority ?? paper.reading_priority ?? 3,
    notes: paper.reading?.notes ?? paper.notes ?? '',
    tags: paper.reading?.tags ?? paper.tags ?? [],
    created_at: paper.reading?.created_at,
    updated_at: paper.reading?.updated_at,
  }
}

function applyReading(paper: Paper, reading: ReadingData): Paper {
  return {
    ...paper,
    reading,
    reading_state: reading.state,
    reading_priority: reading.priority,
    notes: reading.notes,
    tags: reading.tags,
  }
}

function projectPaperSummary(paper: Paper) {
  return { id: paper.id, arxiv_id: paper.arxiv_id, title: paper.title, authors: paper.authors, primary_category: paper.categories[0], published_at: paper.submitted_at }
}

function buildReportText(report: DailyReport, papers: Paper[]) {
  const methods = report.common_methods.map((item) => `- ${item.name ?? item.item ?? '方法线索'}（${item.count} 篇）`)
  const evidence = report.common_evidence.map((item) => `- ${item.name ?? item.item ?? '证据线索'}（${item.count} 篇）`)
  const overview = report.research_situation?.quality_overview
  const overviewLines = overview ? [
    '### 优质论文综合总览', '', overview.lead, '',
    ...overview.paper_findings.flatMap((item, index) => [
      `#### ${index + 1}. ${item.title}`, '',
      `- 推荐分：${Math.round(item.score)}`,
      `- 推进点：${item.contribution}`,
      `- 路线：${item.method}`,
      `- 证据：${item.evidence}`,
      `- 需要留意：${item.limitation}`, '',
    ]),
    `**横向比较：** ${overview.comparison}`, '',
    `**证据判断：** ${overview.evidence_conclusion}`, '',
  ] : []
  const ai = report.ai_synthesis
  const aiLines = ai?.overall_assessment ? [
    '## AI 综合研判', '',
    `### ${ai.headline ?? '总体判断'}`, '', ai.overall_assessment, '',
    '### 核心研究主线', '',
    ...(ai.core_threads ?? []).flatMap((thread, index) => [`#### ${index + 1}. ${thread.title}`, '', thread.analysis, '', `依据论文：${thread.paper_ids.map((paperId) => `#${paperId}`).join('、')}`, '']),
    '### 方法路线比较', '', ai.method_comparison ?? '', '',
    '### 证据质量审计', '', ai.evidence_assessment ?? '', '',
    '### 分歧、风险与未解决问题', '', ai.disagreements_and_risks ?? '', '',
    '### 未来研究路线', '', ai.research_outlook ?? '', '',
    '### 建议行动', '', ...(ai.recommended_actions ?? []).map((action) => `- ${action}`), '',
  ] : []
  const lines = [
    `# ${report.title ?? `arXiv 当日研究总结 · ${formatFullDate(report.report_date)}`}`,
    '',
    '## 执行摘要', '', report.executive_summary, '',
    ...aiLines,
    ...(report.research_situation ? ['## 当日研究态势', '', report.research_situation.synthesis, '', `- 正式方向论文：${report.research_situation.volume.today} 篇`, `- 抓取候选：${report.coverage?.candidate_paper_count ?? report.research_situation.volume.today} 篇`, `- 较昨日：${report.research_situation.volume.change > 0 ? '+' : ''}${report.research_situation.volume.change} 篇`, `- 近 7 日均值：${report.research_situation.volume.recent_daily_average} 篇`, `- 上升方向：${report.research_situation.direction_dynamics.rising.join('、') || '暂无'}`, `- 回落方向：${report.research_situation.direction_dynamics.falling.join('、') || '暂无'}`, `- 共同方法：${report.research_situation.method_signals.join('、') || '尚未形成稳定共性'}`, `- 证据判断：${report.research_situation.evidence_maturity.assessment}`, '', ...overviewLines] : []),
    '## 研究方向动态', '', ...report.topic_insights.flatMap((item) => [`### ${item.topic} · ${item.paper_count} 篇`, '', item.insight, '']),
    '## 代表论文', '',
    ...report.highlight_papers.flatMap((item, index) => {
      const paper = papers.find((candidate) => String(candidate.id) === String(item.paper_id))
      return [`### ${index + 1}. ${item.title}`, '', `- 推荐分：${Math.round(item.score ?? paper?.relevance_score ?? 0)}`, `- arXiv：${item.arxiv_id ?? paper?.arxiv_id ?? ''}`, '', item.summary ?? paper?.summary ?? item.why_it_matters ?? '', '', item.why_it_matters ? `研究意义：${item.why_it_matters}` : '', '']
    }),
    '## 共同方法', '', ...methods, '', '## 共同证据', '', ...evidence, '',
    '## 推荐阅读队列', '', ...report.suggested_reading_queue.map((item) => `${item.rank}. **${item.title}** · ${READING_QUEUE_LABELS[item.priority] ?? item.priority}${item.estimated_minutes ? ` · ${item.estimated_minutes} 分钟` : ''}\n   ${item.next_action ?? item.reason ?? ''}`), '',
    '---', report.methodology_note ?? 'AI 总结仅用于快速筛选，重要结论请以论文原文为准。', '',
    `当日抓取候选 ${report.coverage?.candidate_paper_count ?? report.coverage?.paper_count ?? 0} 篇，其中正式方向论文 ${report.coverage?.paper_count ?? 0} 篇。`,
  ]
  return lines.filter((line) => line !== undefined).join('\n')
}

function formatAuthors(authors: string[]) {
  if (authors.length <= 3) return authors.join(', ')
  return `${authors.slice(0, 3).join(', ')} 等 ${authors.length} 位作者`
}

function relativeTime(date: string) {
  const value = new Date(date).getTime()
  if (Number.isNaN(value)) return ''
  const minutes = Math.max(0, Math.round((Date.now() - value) / 60000))
  if (minutes < 60) return minutes <= 1 ? '刚刚' : `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  return days === 1 ? '昨天' : `${days} 天前`
}

function formatFullDate(date?: string) {
  const value = date ? new Date(`${date}T12:00:00`) : new Date()
  if (Number.isNaN(value.getTime())) return date ?? ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(value)
}

function formatArchiveDate(date: string) {
  const value = new Date(`${date}T12:00:00`)
  if (Number.isNaN(value.getTime())) return date
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const amount = value / 1024 ** power
  return `${amount >= 10 || power === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[power]}`
}

function formatInteger(value: number) {
  return new Intl.NumberFormat('zh-CN').format(Number.isFinite(value) ? value : 0)
}

export default App
