import { AlertTriangle, ArrowDownUp, Bookmark, BookmarkCheck, CalendarDays, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, CircleAlert, Clock3, ExternalLink, FileText, Library, ListFilter, LoaderCircle, NotebookTabs, RefreshCw, RotateCcw, Save, Search, Sparkles, Star, ThumbsDown, X } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../lib/api'
import { isDemo } from '../../lib/runtime'
import { sampleDashboard } from '../../sample'
import type { DashboardData } from '../../types/dashboard'
import type { Feedback, FeedbackAction, Paper, PaperAnalysis, PaperPage, ReadingData, ReadingState } from '../../types/papers'
import type { ProjectMembership, ResearchProject } from '../../types/projects'
import type { JobCalendarData, JobInfo } from '../../types/system'
import type { LoadState, Toast } from '../../app/types'
import { TagEditor, WorkspaceSkeleton, PageError, EmptyPapers, OrganizationBadge } from '../../shared/ui'
import { getReading, applyReading, projectPaperSummary, formatAuthors, relativeTime, formatFullDate, formatArchiveDate, formatDateTime } from '../../shared/format'
import { READING_STATE_OPTIONS, PRIORITY_LABELS, FEEDBACK_REASON_OPTIONS } from '../../shared/options'

interface PaperWorkspaceProps {

  mode: 'today' | 'library'
  data: DashboardData | null
  state: LoadState
  error: string
  selectedId: Paper['id'] | null
  refreshing: boolean
  job: JobInfo | null
  onRetry: () => void
  onRefresh: (date?: string) => void
  onDateSelect: (date: string) => void
  onSelect: (id: Paper['id']) => void
  onCloseDetail: () => void
  onFeedback: (paper: Paper, action: FeedbackAction) => Promise<boolean>
  onSaveReading: (paper: Paper, reading: ReadingData) => Promise<boolean>
  onSaveDetailedFeedback: (paper: Paper, feedback: Feedback) => Promise<boolean>
  projects: ResearchProject[]
  onProjectsChange: (projects: ResearchProject[]) => void
  onToast: (kind: Toast['kind'], message: string) => void
}

type PaperSort = 'recommendation' | 'relevance' | 'time' | 'watched_authors' | 'watched_organizations'

export function PaperWorkspace(props: PaperWorkspaceProps) {
  const { mode, data, state, error, selectedId, refreshing, job, onRetry, onRefresh, onDateSelect, onSelect, onCloseDetail, onFeedback, onSaveReading, onSaveDetailedFeedback, projects, onProjectsChange, onToast } = props
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [priority, setPriority] = useState(mode === 'today' ? 'recommended' : 'all')
  const [sort, setSort] = useState<PaperSort>('recommendation')
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
    setSort('recommendation')
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
        sort: sort === 'watched_authors' || sort === 'watched_organizations' ? 'recommendation' : sort,
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
    const watchedAuthors = new Set((data?.profile?.watched_authors ?? []).map(authorIdentity))
    const watchedOrganizations = (data?.profile?.watched_organizations ?? []).map(organizationIdentity)
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
        if (sort === 'watched_authors') {
          const priorityDifference = Number(matchesWatchedAuthor(b, watchedAuthors)) - Number(matchesWatchedAuthor(a, watchedAuthors))
          if (priorityDifference) return priorityDifference
        }
        if (sort === 'watched_organizations') {
          const priorityDifference = Number(matchesWatchedOrganization(b, watchedOrganizations)) - Number(matchesWatchedOrganization(a, watchedOrganizations))
          if (priorityDifference) return priorityDifference
        }
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
  const sortOptions: Array<[PaperSort, string]> = [
    ['recommendation', '推荐优先'],
    ['relevance', '相关度优先'],
    ['time', '最新提交'],
  ]
  if (mode === 'today') {
    sortOptions.push(
      ['watched_authors', '关注作者优先'],
      ['watched_organizations', '关注机构优先'],
    )
  }

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
        <button className="button primary" onClick={() => onRefresh()} disabled={refreshing}>
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
            <small>初筛每批 {data.processing_summary?.screening_limit ?? 30} · 深读最多 {data.processing_summary?.deep_analysis_limit ?? 5} · 门槛 {Math.round(data.processing_summary?.deep_analysis_threshold ?? 90)}</small>
          </div>
        </section>
      )}

      {mode === 'today' && <RunCalendar busy={refreshing} refreshToken={job?.status === 'completed' || job?.status === 'failed' ? job.id : ''} onRun={onRefresh} onSelectDate={onDateSelect} />}

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
            <SelectControl value={sort} onChange={(value) => setSort(value as typeof sort)} label="排序" icon={<ArrowDownUp size={14} />} options={sortOptions} />
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

function authorIdentity(value: string) {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

function organizationIdentity(value: string) {
  return value.normalize('NFKC').trim().replace(/[^\p{L}\p{N}]+/gu, ' ').toLocaleLowerCase()
}

function matchesWatchedAuthor(paper: Paper, watched: Set<string>) {
  return paper.authors.some((author) => watched.has(authorIdentity(author)))
}

function matchesWatchedOrganization(paper: Paper, watched: string[]) {
  return (paper.affiliations ?? []).some((affiliation) => {
    const normalized = ` ${organizationIdentity(affiliation)} `
    return watched.some((organization) => normalized.includes(` ${organization} `))
  })
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

function RunCalendar({ busy, refreshToken, onRun, onSelectDate }: { busy: boolean; refreshToken: string; onRun: (date: string) => void; onSelectDate: (date: string) => void }) {
  const now = new Date()
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const [month, setMonth] = useState(currentMonth)
  const [data, setData] = useState<JobCalendarData | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setError('')
    if (isDemo) {
      const latest = new Date(now)
      latest.setDate(latest.getDate() - 1)
      setData({ month, latest_available_date: `${latest.getFullYear()}-${String(latest.getMonth() + 1).padStart(2, '0')}-${String(latest.getDate()).padStart(2, '0')}`, days: [] })
      return () => { active = false }
    }
    void api.jobCalendar(month)
      .then((result) => { if (active) setData(result) })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '任务日历加载失败')
      })
    return () => { active = false }
  }, [month, refreshToken])

  const [year, monthNumber] = month.split('-').map(Number)
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  const firstWeekday = (new Date(year, monthNumber - 1, 1).getDay() + 6) % 7
  const dayCount = new Date(year, monthNumber, 0).getDate()
  const daysByDate = new globalThis.Map(data?.days.map((day) => [day.date, day]) ?? [])
  const selectedRecord = selectedDate ? daysByDate.get(selectedDate) : undefined
  const cells = [...Array.from({ length: firstWeekday }, () => null), ...Array.from({ length: dayCount }, (_, index) => index + 1)]
  const monthLabel = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long' }).format(new Date(year, monthNumber - 1, 1))
  const moveMonth = (offset: number) => {
    const target = new Date(year, monthNumber - 1 + offset, 1)
    setMonth(`${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, '0')}`)
    setSelectedDate(null)
  }

  return (
    <section className="run-calendar" aria-label="论文筛选覆盖日历">
      <header>
        <div><span>论文筛选覆盖</span><h2>{monthLabel}</h2></div>
        <div className="calendar-controls">
          <span><i className="completed" />完整筛选</span>
          <span><i className="partial" />待补充</span>
          <button className="icon-button" onClick={() => moveMonth(-1)} aria-label="上个月"><ChevronLeft size={16} /></button>
          <button className="icon-button" onClick={() => moveMonth(1)} aria-label="下个月"><ChevronRight size={16} /></button>
        </div>
      </header>
      <div className="calendar-weekdays" aria-hidden="true">{['一', '二', '三', '四', '五', '六', '日'].map((day) => <span key={day}>{day}</span>)}</div>
      <div className="calendar-grid">
        {cells.map((day, index) => {
          if (day === null) return <span className="calendar-empty" key={`empty-${index}`} />
          const date = `${month}-${String(day).padStart(2, '0')}`
          const record = daysByDate.get(date)
          const unavailable = !data || date > data.latest_available_date
          const title = unavailable ? '该论文日期尚未完整结束' : record ? `${record.message}；当前语义候选 ${record.candidates}，AI 初筛 ${record.screened}，待初筛 ${record.pending_screening}` : '尚未筛选，点击后可以补跑'
          return <button className={`calendar-day ${record?.status ?? ''} ${date === today ? 'today' : ''} ${date === selectedDate ? 'selected' : ''}`} disabled={unavailable} onClick={() => { setSelectedDate(date); onSelectDate(date) }} title={title} key={date}><strong>{day}</strong>{record && <small>候选 {record.candidates}</small>}</button>
        })}
      </div>
      {selectedDate && data && <div className={`calendar-detail ${selectedRecord?.status ?? 'missing'}`}>
        <div><span>{formatArchiveDate(selectedDate)}</span><strong>{selectedRecord?.status === 'completed' ? '当天已完整筛选' : selectedRecord?.pending_screening ? '有新的语义候选待初筛' : selectedRecord ? '当天需要继续补充' : '当天尚未筛选'}</strong><small>{selectedRecord ? `抓取 ${selectedRecord.fetched} · 语义候选 ${selectedRecord.candidates} · AI 初筛 ${selectedRecord.screened} · 待初筛 ${selectedRecord.pending_screening} · 深读 ${selectedRecord.analyzed}；${selectedRecord.message}` : '只补跑这一天，不会自动追赶其他空缺日期。'}</small></div>
        {selectedRecord?.status !== 'completed' && <button className="button primary compact" disabled={busy} onClick={() => onRun(selectedDate)}><RefreshCw className={busy ? 'spin' : ''} size={14} />{busy ? '任务进行中' : selectedRecord ? '继续补跑' : '补跑这一天'}</button>}
        <button className="icon-button" onClick={() => setSelectedDate(null)} aria-label="关闭日期详情"><X size={15} /></button>
      </div>}
      {error && <p className="calendar-error">{error}</p>}
    </section>
  )
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
