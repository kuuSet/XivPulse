import { CheckCircle2, CircleAlert, KeyRound, LayoutDashboard, Library, LoaderCircle, LogOut, Mail, Menu, MoreHorizontal, NotebookTabs, Radar, Settings as SettingsIcon, SlidersHorizontal, TrendingUp, X } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { DigestView } from './features/digest/DigestView'
import { PaperWorkspace } from './features/papers/PaperWorkspace'
import { InterestsView } from './features/interests/InterestsView'
import { ProjectsView } from './features/projects/ProjectsView'
import { SettingsView } from './features/settings/SettingsView'
import { TrendsView } from './features/trends/TrendsView'
import { api } from './lib/api'
import { BRAND_LOGO, isDemo } from './lib/runtime'
import { sampleDashboard, sampleProjects } from './sample'
import { applyReading, getReading } from './shared/format'
import type { DashboardData } from './types/dashboard'
import type { Feedback, FeedbackAction, Paper, ReadingData } from './types/papers'
import type { ResearchProject } from './types/projects'
import type { AuthSession, JobInfo } from './types/system'
import type { LoadState, Toast, View } from './app/types'

const NAV_ITEMS: Array<{ id: View; label: string; icon: typeof Radar }> = [
  { id: 'today', label: '今日论文', icon: LayoutDashboard },
  { id: 'library', label: '论文库', icon: Library },
  { id: 'digest', label: '研究日报', icon: Mail },
  { id: 'projects', label: '分类库', icon: NotebookTabs },
  { id: 'trends', label: '研究方向', icon: TrendingUp },
  { id: 'interests', label: '关注设置', icon: SlidersHorizontal },
  { id: 'settings', label: '系统设置', icon: SettingsIcon },
]

const initialParams = new URLSearchParams(window.location.search)
const requestedView = initialParams.get('view') as View | null
const requestedPaper = initialParams.get('paper')
const requestedDate = validDate(initialParams.get('date'))
const initialView: View = NAV_ITEMS.some((item) => item.id === requestedView) ? requestedView! : 'today'

function validDate(value: string | null) {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : undefined
}

function locationDate() {
  return validDate(new URLSearchParams(window.location.search).get('date'))
}

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
  const [uiFontSizePt, setUiFontSizePt] = useState(12)

  useEffect(() => {
    document.documentElement.style.setProperty('--ui-scale', String(uiFontSizePt / 12))
  }, [uiFontSizePt])

  const pushToast = useCallback((kind: Toast['kind'], message: string) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, kind, message }])
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 3200)
  }, [])

  const loadDashboard = useCallback(async (targetDate?: string) => {
    setDashboardState('loading')
    setDashboardError('')
    try {
      const data = isDemo ? sampleDashboard : await api.dashboard(targetDate)
      setDashboard(data)
      setUiFontSizePt(data.settings?.ui_font_size_pt ?? 12)
      const url = new URL(window.location.href)
      if ((url.searchParams.get('view') ?? 'today') === 'today' && data.date) {
        url.searchParams.set('date', data.date)
        window.history.replaceState(null, '', url)
      }
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
    void loadDashboard(requestedDate)
    void loadProjects()
  }, [authSession?.authenticated, loadDashboard, loadProjects])

  useEffect(() => {
    const restoreLocation = () => {
      const params = new URLSearchParams(window.location.search)
      const nextView = params.get('view') as View | null
      const restoredView = NAV_ITEMS.some((item) => item.id === nextView) ? nextView! : 'today'
      const restoredDate = validDate(params.get('date'))
      setView(restoredView)
      setSelectedId(params.get('paper'))
      if (restoredView === 'today' && restoredDate !== dashboard?.date) void loadDashboard(restoredDate)
    }
    window.addEventListener('popstate', restoreLocation)
    return () => window.removeEventListener('popstate', restoreLocation)
  }, [dashboard?.date, loadDashboard])

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
          if (wasBusy) void loadDashboard(locationDate())
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

  const handleRefresh = async (targetDate?: string) => {
    setRefreshing(true)
    try {
      if (!isDemo) {
        const result = targetDate ? await api.runDaily(targetDate) : await api.refresh()
        setJob({ id: result.job_id, job_id: result.job_id, kind: 'daily', status: result.status, progress: 0, total: 0, message: result.message, created_at: new Date().toISOString() })
        setRefreshing(true)
        pushToast('success', targetDate ? `${targetDate} 补跑任务已启动` : (result.message || '抓取任务已启动'))
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
    const changedView = targetView !== view
    setView(targetView)
    setSelectedId(id)
    const url = new URL(window.location.href)
    url.searchParams.set('view', targetView)
    url.searchParams.set('paper', String(id))
    window.history.pushState(null, '', url)
    if (changedView) window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [view])

  const selectDate = useCallback((date: string) => {
    const url = new URL(window.location.href)
    url.searchParams.set('view', 'today')
    url.searchParams.set('date', date)
    url.searchParams.delete('paper')
    window.history.pushState(null, '', url)
    setSelectedId(null)
    void loadDashboard(date)
  }, [loadDashboard])

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
            onDateSelect={selectDate}
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
        {view === 'interests' && <InterestsView onToast={pushToast} />}
        {view === 'settings' && <SettingsView onToast={pushToast} onFontSizeChange={setUiFontSizePt} />}
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

export default App
