import { CalendarDays, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Copy, Download, ExternalLink, Library, LoaderCircle, Mail, Network, Radar, RefreshCw, Save, Sparkles, TrendingUp } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { BRAND_LOGO, isDemo } from '../../lib/runtime'
import { sampleDailyReport } from '../../sample'
import type { DashboardData } from '../../types/dashboard'
import type { Paper } from '../../types/papers'
import type { DailyAggregateItem, DailyReport, DailyReportArchiveItem } from '../../types/reports'
import type { LoadState, Toast } from '../../app/types'
import { WorkspaceSkeleton, PageError, EmptyPapers, OrganizationBadge } from '../../shared/ui'
import { buildReportText, formatFullDate, formatArchiveDate, formatDateTime, formatInteger } from '../../shared/format'
import { READING_QUEUE_LABELS } from '../../shared/options'

export function DigestView({ data, state, error, onRetry, onToast }: { data: DashboardData | null; state: LoadState; error: string; onRetry: () => void; onToast: (kind: Toast['kind'], message: string) => void }) {
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
      else onToast('error', 'AI 综合研判未生成')
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
            <div><dt>时区</dt><dd>{data?.settings?.schedule_timezone ?? 'Asia/Shanghai'}</dd></div>
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
                  <div><strong>{Math.round(item.score ?? paper?.effective_score ?? 0)} 分</strong><em>{item.recommendation ?? '重点关注'}</em></div>
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
  }
  if (!synthesis) {
    return <section className="report-section ai-synthesis-section empty"><div className="report-section-title"><Sparkles size={16} /><h3>AI 综合研判</h3></div><div className="ai-synthesis-status"><strong>尚未生成长篇综合研判</strong><p>日更完成后可自动生成，也可使用日报顶部按钮按需调用一次。</p></div></section>
  }
  const status = statusCopy[synthesis.status]
  return <section className={`report-section ai-synthesis-section ${synthesis.stale ? 'stale' : ''}`}>
    <div className="report-section-title"><Sparkles size={16} /><h3>AI 综合研判</h3>{synthesis.budget_mode && <span>{synthesis.budget_mode}</span>}</div>
    {(status || synthesis.stale) && <div className="ai-synthesis-status"><strong>{synthesis.stale ? '底层日报已经变化，这份研判需要更新' : status?.title}</strong><p>{synthesis.stale ? '旧内容仍保留供对照；点击日报顶部“重做 AI 研判”才会再次调用模型。' : status?.detail}</p></div>}
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

function ResearchSituation({ situation }: { situation: NonNullable<DailyReport['research_situation']> }) {
  const volumeChange = situation.volume.change
  const evidenceLabels: Record<string, string> = { no_signal: '暂无信号', insufficient_analysis: '分析不足', preliminary: '初步', mixed: '混合证据', strong: '较强证据' }
  const overview = situation.quality_overview
  return <section className="report-section situation-section"><div className="report-section-title"><Radar size={16} /><h3>当日研究态势</h3></div><p className="situation-synthesis">{situation.synthesis}</p><div className="situation-grid"><article><span>研究活动</span><strong>{situation.volume.today} 篇</strong><small>较昨日 {volumeChange > 0 ? '+' : ''}{volumeChange} · 近 7 日均值 {situation.volume.recent_daily_average}</small></article><article><span>方向变化</span><strong>{situation.direction_dynamics.rising.length ? situation.direction_dynamics.rising.join('、') : '暂无上升方向'}</strong><small>{situation.direction_dynamics.falling.length ? `回落：${situation.direction_dynamics.falling.join('、')}` : `头部方向占 ${Math.round(situation.direction_dynamics.top_direction_share_percent)}%`}</small></article><article><span>共同方法</span><strong>{situation.method_signals.length ? situation.method_signals.join('、') : '尚未形成稳定共性'}</strong><small>跨论文方法信号</small></article><article><span>证据成熟度</span><strong>{evidenceLabels[situation.evidence_maturity.status] ?? situation.evidence_maturity.status}</strong><small>{situation.evidence_maturity.assessment}</small></article></div>{overview && <QualityOverview overview={overview} />}</section>
}

function QualityOverview({ overview }: { overview: NonNullable<NonNullable<DailyReport['research_situation']>['quality_overview']> }) {
  return <div className="quality-overview"><header><div><span>优质论文综合总览</span><h4>今日高分论文推进了什么</h4></div><small>推荐分 ≥ {Math.round(overview.score_threshold)} · 纳入 {overview.included_paper_count}/{overview.qualified_paper_count} 篇</small></header><p className="quality-overview-lead">{overview.lead}</p>{overview.main_threads.length > 0 && <div className="quality-threads">{overview.main_threads.map((thread) => <span key={thread.name}><strong>{thread.name}</strong><em>{thread.paper_count} 篇{thread.average_score != null ? ` · 均分 ${thread.average_score}` : ''}</em></span>)}</div>}<div className="quality-findings">{overview.paper_findings.map((item, index) => <article key={String(item.paper_id)}><div className="quality-finding-heading"><span>{String(index + 1).padStart(2, '0')}</span><div><h5>{item.title}</h5><small>{Math.round(item.score)} 分 · {item.analysis_kind === 'deep' ? '全文深读' : item.analysis_kind === 'screening' ? '摘要初筛' : '本地线索'}</small></div></div><p>{item.contribution}</p><dl><div><dt>路线</dt><dd>{item.method}</dd></div><div><dt>证据</dt><dd>{item.evidence}</dd></div><div><dt>留意</dt><dd>{item.limitation}</dd></div></dl></article>)}</div><div className="quality-conclusion"><p><strong>横向比较</strong>{overview.comparison}</p><p><strong>证据判断</strong>{overview.evidence_conclusion}</p></div></div>
}

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
