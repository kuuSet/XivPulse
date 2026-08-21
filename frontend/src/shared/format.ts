import type { Paper, ReadingData } from '../types/papers'
import type { DailyReport } from '../types/reports'
import { READING_QUEUE_LABELS } from './options'

export function getReading(paper: Paper): ReadingData {
  return paper.reading
}

export function applyReading(paper: Paper, reading: ReadingData): Paper {
  return {
    ...paper,
    reading,
  }
}

export function projectPaperSummary(paper: Paper) {
  return { id: paper.id, arxiv_id: paper.arxiv_id, title: paper.title, authors: paper.authors, primary_category: paper.categories[0], published_at: paper.submitted_at }
}

export function buildReportText(report: DailyReport) {

  const methods = report.common_methods.map((item) => `- ${item.name}（${item.count} 篇）`)
  const evidence = report.common_evidence.map((item) => `- ${item.name}（${item.count} 篇）`)
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
    ...(report.research_situation ? ['## 当日研究态势', '', report.research_situation.synthesis, '', `- 粗排入选：${report.research_situation.volume.today} 篇`, `- 较昨日：${report.research_situation.volume.change > 0 ? '+' : ''}${report.research_situation.volume.change} 篇`, `- 近 7 日均值：${report.research_situation.volume.recent_daily_average} 篇`, `- 平均分：${report.research_situation.score_summary.average == null ? '暂无' : Math.round(report.research_situation.score_summary.average)}`, `- 高分论文：${report.research_situation.score_summary.high_score_count} 篇`, `- 共同方法：${report.research_situation.method_signals.join('、') || '尚未形成稳定共性'}`, `- 证据判断：${report.research_situation.evidence_maturity.assessment}`, '', ...overviewLines] : []),
    '## 代表论文', '',
    ...report.highlight_papers.flatMap((item, index) => [`### ${index + 1}. ${item.title}`, '', `- 推荐分：${Math.round(item.score)}`, `- arXiv：${item.arxiv_id}`, '', item.summary, '']),
    '## 共同方法', '', ...methods, '', '## 共同证据', '', ...evidence, '',
    '## 推荐阅读队列', '', ...report.suggested_reading_queue.map((item) => `${item.rank}. **${item.title}** · ${READING_QUEUE_LABELS[item.priority]} · ${item.estimated_minutes} 分钟\n   ${item.next_action}`), '',
    '---', report.methodology_note, '',
    `当日共有 ${report.coverage.paper_count} 篇论文通过摘要粗排。`,
  ]
  return lines.filter((line) => line !== undefined).join('\n')
}

export function formatAuthors(authors: string[]) {
  if (authors.length <= 3) return authors.join(', ')
  return `${authors.slice(0, 3).join(', ')} 等 ${authors.length} 位作者`
}

export function relativeTime(date: string) {
  const value = new Date(date).getTime()
  if (Number.isNaN(value)) return ''
  const minutes = Math.max(0, Math.round((Date.now() - value) / 60000))
  if (minutes < 60) return minutes <= 1 ? '刚刚' : `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  return days === 1 ? '昨天' : `${days} 天前`
}

export function formatFullDate(date?: string) {
  const value = date ? new Date(`${date}T12:00:00`) : new Date()
  if (Number.isNaN(value.getTime())) return date ?? ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(value)
}

export function formatArchiveDate(date: string) {
  const value = new Date(`${date}T12:00:00`)
  if (Number.isNaN(value.getTime())) return date
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
}

export function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

export function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const amount = value / 1024 ** power
  return `${amount >= 10 || power === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[power]}`
}

export function formatInteger(value: number) {
  return new Intl.NumberFormat('zh-CN').format(Number.isFinite(value) ? value : 0)
}
