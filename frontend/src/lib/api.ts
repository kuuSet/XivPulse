import type {
  DirectionCatalog,
  Interests,
} from '../types/interests'
import type { DashboardData } from '../types/dashboard'
import type { MethodCandidates, MethodCatalog } from '../types/methods'
import type { FeedbackReason, FeedbackWriteAction, Paper, PaperPage, ReadingData } from '../types/papers'
import type {
  ProjectDraft,
  ProjectMembership,
  ResearchProject,
} from '../types/projects'
import type { DailyReport, DailyReportArchiveItem, DailyResearchGap } from '../types/reports'
import type { AnalysisPromptCatalog, Settings } from '../types/settings'
import type { AppStatus, AuthSession, JobCalendarData, JobInfo } from '../types/system'
import type { ResearchRoadmap, TopicTrends } from '../types/trends'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api'

class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = (await response.json()) as { detail?: string; message?: string }
      detail = body.detail ?? body.message ?? detail
    } catch {
      // Keep the status-based message for non-JSON responses.
    }
    throw new ApiError(detail, response.status)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

interface FeedbackPayload {
  action: FeedbackWriteAction
  rating?: number | null
  note?: string
  reasons?: FeedbackReason[]
}

type ReadingPayload = Partial<Pick<ReadingData, 'state' | 'priority' | 'notes' | 'tags'>>
type MembershipPayload = Partial<Pick<ProjectMembership, 'reading_state' | 'priority' | 'notes' | 'tags'>>

interface PaperQuery {
  q?: string
  category?: string
  priority?: 'high' | 'medium' | 'low'
  feedback?: string
  reading_state?: ReadingData['state']
  sort?: 'recommendation' | 'relevance' | 'time'
  limit?: number
  offset?: number
}

export const api = {
  dashboard: (date?: string) =>
    request<DashboardData>(`/dashboard${date ? `?date=${encodeURIComponent(date)}` : ''}`),
  refresh: () => request<{ job_id: string; status: string; message: string }>('/refresh', { method: 'POST' }),
  runDaily: (date: string) => request<{ job_id: string; status: string; message: string; date: string }>('/run-daily', {
    method: 'POST', body: JSON.stringify({ date }),
  }),
  status: () => request<AppStatus>('/status'),
  jobStatus: (jobId: string) => request<JobInfo>(`/jobs/${encodeURIComponent(jobId)}`),
  jobCalendar: (month: string) => request<JobCalendarData>(`/job-calendar?month=${encodeURIComponent(month)}`),
  papers: (query: PaperQuery = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.size ? `?${params.toString()}` : ''
    return request<PaperPage>(`/papers${suffix}`)
  },
  paper: (paperId: PaperId) => request<Paper>(`/papers/${encodeId(paperId)}`),
  authSession: () => request<AuthSession>('/auth/session'),
  login: (username: string, password: string) => request<AuthSession>('/auth/login', {
    method: 'POST', body: JSON.stringify({ username, password }),
  }),
  logout: () => request<AuthSession>('/auth/logout', { method: 'POST' }),
  interests: () => request<Interests>('/interests'),
  directionCatalog: () => request<DirectionCatalog>('/directions/catalog'),
  saveInterests: (interests: Interests) =>
    request<Interests>('/interests', {
      method: 'PUT',
      body: JSON.stringify(interestsUpdatePayload(interests)),
    }),
  settings: () => request<Settings>('/settings'),
  promptCatalog: () => request<AnalysisPromptCatalog>('/prompts/catalog'),
  methodCatalog: () => request<MethodCatalog>('/methods/catalog'),
  methodCandidates: () => request<MethodCandidates>('/methods/candidates'),
  acceptMethodCandidate: (key: string, name?: string) => request<MethodCatalog>('/methods/candidates/accept', {
    method: 'POST', body: JSON.stringify({ key, ...(name ? { name } : {}) }),
  }),
  dismissMethodCandidate: (key: string) => request<{ ok: boolean }>('/methods/candidates/dismiss', {
    method: 'POST', body: JSON.stringify({ key }),
  }),
  saveCustomMethods: (items: Array<{ name: string; aliases: string[] }>) => request<MethodCatalog>('/methods/custom', {
    method: 'PUT', body: JSON.stringify({ items }),
  }),
  saveSettings: (settings: Settings & { api_key?: string; smtp_password?: string }) =>
    request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(settingsUpdatePayload(settings)) }),
  testConnection: (settings: Pick<Settings, 'model_source' | 'provider_name' | 'model_name' | 'api_base_url' | 'codex_model' | 'codex_reasoning_effort'> & { api_key?: string }) =>
    request<{ ok: boolean; message: string }>('/settings/test', { method: 'POST', body: JSON.stringify(settings) }),
  testEmail: () => request<{ ok: boolean; email_sent: number; email_failed: number; email_status: string }>('/settings/email/test', { method: 'POST' }),
  feedback: (paperId: PaperId, actionOrPayload: FeedbackWriteAction | FeedbackPayload, note?: string) => {
    const payload: FeedbackPayload = typeof actionOrPayload === 'string'
      ? { action: actionOrPayload, ...(note !== undefined ? { note } : {}) }
      : actionOrPayload
    return request<{ ok?: boolean; action?: string; rating?: number | null; note?: string; reasons?: FeedbackReason[] }>(
      `/papers/${encodeURIComponent(String(paperId))}/feedback`,
      {
        method: 'POST',
        body: JSON.stringify({ ...payload, action: payload.action === 'dismiss' ? 'skip' : payload.action }),
      },
    )
  },
  saveReading: (paperId: PaperId, payload: ReadingPayload) =>
    request<ReadingData & { ok?: boolean; paper_id?: PaperId }>(`/papers/${encodeURIComponent(String(paperId))}/reading`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  projects: async () => unwrapList<ResearchProject>(await request<unknown>('/projects')),
  project: async (projectId: ProjectId) => unwrapEntity<ResearchProject>(await request<unknown>(`/projects/${encodeId(projectId)}`), 'project'),
  createProject: async (payload: ProjectDraft) =>
    unwrapEntity<ResearchProject>(await request<unknown>('/projects', { method: 'POST', body: JSON.stringify(payload) }), 'project'),
  updateProject: async (projectId: ProjectId, payload: Partial<ProjectDraft>) =>
    unwrapEntity<ResearchProject>(await request<unknown>(`/projects/${encodeId(projectId)}`, {
      method: 'PATCH', body: JSON.stringify(payload),
    }), 'project'),
  deleteProject: (projectId: ProjectId) => request<void>(`/projects/${encodeId(projectId)}`, { method: 'DELETE' }),
  addProjectPaper: async (projectId: ProjectId, paperId: PaperId, payload: MembershipPayload = {}) => {
    const body = withMembershipDefaults(payload)
    const result = await request<unknown>(
      `/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`,
      { method: 'POST', body: JSON.stringify(body) },
    )
    return unwrapEntity<ProjectMembership>(result, 'membership')
  },
  updateProjectPaper: async (projectId: ProjectId, paperId: PaperId, payload: MembershipPayload) => {
    const result = await request<unknown>(
      `/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    )
    return unwrapEntity<ProjectMembership>(result, 'membership')
  },
  removeProjectPaper: (projectId: ProjectId, paperId: PaperId) =>
    request<void>(`/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`, { method: 'DELETE' }),
  topicTrends: async (date?: string) =>
    unwrapEntity<TopicTrends>(
      await request<unknown>(`/trends/topics${date ? `?date=${encodeURIComponent(date)}` : ''}`),
      'trends',
    ),
  roadmap: async () => unwrapEntity<ResearchRoadmap>(await request<unknown>('/trends/roadmap'), 'roadmap'),
  dailyReport: async (date: string) => normalizeDailyReport(
    unwrapEntity<DailyReport>(await request<unknown>(`/reports/daily?date=${encodeURIComponent(date)}`), 'report'),
  ),
  dailyReportHistory: async (limit = 90) => unwrapList<DailyReportArchiveItem>(
    await request<unknown>(`/reports/daily/history?limit=${encodeURIComponent(String(limit))}`),
  ),
  regenerateDailyReport: async (date: string) => normalizeDailyReport(
    unwrapEntity<DailyReport>(await request<unknown>(`/reports/daily/regenerate?date=${encodeURIComponent(date)}`, { method: 'POST' }), 'report'),
  ),
  synthesizeDailyReport: async (date: string, force = true) => normalizeDailyReport(
    unwrapEntity<DailyReport>(await request<unknown>(`/reports/daily/synthesize?date=${encodeURIComponent(date)}&force=${force ? 'true' : 'false'}`, { method: 'POST' }), 'report'),
  ),
  sendDailyReport: (date: string) => request<{ ok: boolean; email_sent: number; email_failed: number; email_status: string }>(`/reports/daily/send?date=${encodeURIComponent(date)}`, { method: 'POST' }),
}

type PaperId = number | string
type ProjectId = number | string

function encodeId(id: PaperId | ProjectId) {
  return encodeURIComponent(String(id))
}

function unwrapList<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (!value || typeof value !== 'object') return []
  const record = value as Record<string, unknown>
  for (const key of ['items', 'projects', 'data', 'results']) {
    if (Array.isArray(record[key])) return record[key] as T[]
  }
  return []
}

function unwrapEntity<T>(value: unknown, key: string): T {
  if (!value || typeof value !== 'object') return value as T
  const record = value as Record<string, unknown>
  const nested = record[key] ?? record.data ?? record.result
  return (nested && typeof nested === 'object' && !Array.isArray(nested) ? nested : value) as T
}

function withMembershipDefaults(payload: MembershipPayload) {
  return {
    reading_state: payload.reading_state ?? 'queued',
    priority: payload.priority ?? 3,
    notes: payload.notes ?? '',
    tags: payload.tags ?? [],
  }
}

function normalizeDailyReport(report: DailyReport): DailyReport {
  const highlightPapers = report.highlight_papers ?? report.notable_papers ?? []
  const limitations = (report.limitations_and_research_gaps ?? report.limitations_research_gaps ?? []).map(normalizeGap)
  const queue = report.suggested_reading_queue ?? report.recommended_reading_queue ?? []
  return {
    ...report,
    highlight_papers: highlightPapers,
    notable_papers: highlightPapers,
    common_methods: report.common_methods ?? report.shared_methods_evidence ?? [],
    common_evidence: report.common_evidence ?? [],
    limitations_and_research_gaps: limitations,
    limitations_research_gaps: limitations,
    suggested_reading_queue: queue,
    recommended_reading_queue: queue,
  }
}

function normalizeGap(value: string | DailyResearchGap) {
  if (typeof value === 'string') return value
  return value.text ?? value.gap ?? value.limitation ?? value.name ?? value.item ?? '待核查的研究缺口'
}

function interestsUpdatePayload(interests: Interests) {
  return {
    topics: interests.topics.map((topic) => ({
      ...(topic.id !== undefined ? { id: topic.id } : {}),
      name: topic.name.trim(),
      description: topic.description.trim(),
      aliases: unique(topic.aliases ?? []),
      // The visual editor treats keywords as the source of truth for arXiv queries.
      query: '',
      arxiv_categories: unique(topic.arxiv_categories ?? []),
      keywords: unique(topic.keywords ?? []),
      context_keywords: unique(topic.context_keywords ?? []),
      negative_keywords: unique(topic.negative_keywords ?? []),
      weight: topic.weight ?? 1,
      enabled: topic.enabled !== false,
    })),
    watched_authors: unique(interests.watched_authors),
    watched_organizations: unique(interests.watched_organizations),
  }
}

function settingsUpdatePayload(settings: Settings & { api_key?: string; smtp_password?: string }) {
  return {
    model_source: settings.model_source,
    provider_name: settings.provider_name,
    model_name: settings.model_name,
    api_base_url: settings.api_base_url,
    codex_model: settings.codex_model,
    codex_reasoning_effort: settings.codex_reasoning_effort,
    ui_font_size_pt: settings.ui_font_size_pt,
    schedule_time: settings.schedule_time,
    schedule_timezone: settings.schedule_timezone,
    arxiv_fetch_limit: settings.arxiv_fetch_limit,
    prefilter_batch_size: Math.min(settings.arxiv_fetch_limit, settings.prefilter_batch_size),
    deep_analysis_limit: settings.deep_analysis_limit,
    deep_analysis_threshold: settings.deep_analysis_threshold ?? 90,
    analysis_budget: settings.analysis_budget,
    daily_synthesis_enabled: settings.daily_synthesis_enabled,
    prompt_template_id: settings.prompt_template_id,
    screening_prompt: settings.screening_prompt,
    deep_analysis_prompt: settings.deep_analysis_prompt,
    email_enabled: settings.email_enabled,
    email_recipients: settings.email_recipients,
    smtp_host: settings.smtp_host,
    smtp_port: settings.smtp_port,
    smtp_security: settings.smtp_security,
    smtp_username: settings.smtp_username,
    smtp_sender: settings.smtp_sender,
    public_url: settings.public_url,
    ...(settings.api_key ? { api_key: settings.api_key } : {}),
    ...(settings.smtp_password ? { smtp_password: settings.smtp_password } : {}),
  }
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)))
}
