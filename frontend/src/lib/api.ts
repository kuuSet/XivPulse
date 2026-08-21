import type {
  DirectionCatalog,
  Interests,
} from '../types/interests'
import type { DashboardData } from '../types/dashboard'
import type { FeedbackReason, FeedbackWriteAction, Paper, PaperPage, ReadingData } from '../types/papers'
import type {
  ProjectDraft,
  ProjectMembership,
  ResearchProject,
} from '../types/projects'
import type { DailyReport, DailyReportArchiveItem } from '../types/reports'
import type { AnalysisPromptCatalog, ModelStage, Settings } from '../types/settings'
import type { AppStatus, AuthSession, JobCalendarData, JobInfo } from '../types/system'

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
  refresh: () => request<{ id: string; status: string; message: string }>('/refresh', { method: 'POST' }),
  runDaily: (date: string) => request<{ id: string; status: string; message: string; date: string }>('/run-daily', {
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
  saveSettings: (settings: Settings & { api_keys?: Partial<Record<ModelStage, string>>; smtp_password?: string }) =>
    request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(settingsUpdatePayload(settings)) }),
  testConnection: (stage: ModelStage) =>
    request<{ ok: boolean; message: string }>('/settings/test', { method: 'POST', body: JSON.stringify({ stage }) }),
  testEmail: () => request<{ ok: boolean; email_sent: number; email_failed: number; email_status: string }>('/settings/email/test', { method: 'POST' }),
  feedback: (paperId: PaperId, actionOrPayload: FeedbackWriteAction | FeedbackPayload, note?: string) => {
    const payload: FeedbackPayload = typeof actionOrPayload === 'string'
      ? { action: actionOrPayload, ...(note !== undefined ? { note } : {}) }
      : actionOrPayload
    return request<{ ok?: boolean; action?: string; rating?: number | null; note?: string; reasons?: FeedbackReason[] }>(
      `/papers/${encodeURIComponent(String(paperId))}/feedback`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    )
  },
  saveReading: (paperId: PaperId, payload: ReadingPayload) =>
    request<ReadingData & { ok?: boolean; paper_id?: PaperId }>(`/papers/${encodeURIComponent(String(paperId))}/reading`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  projects: async () => (await request<{ items: ResearchProject[]; count: number }>('/projects')).items,
  project: (projectId: ProjectId) => request<ResearchProject>(`/projects/${encodeId(projectId)}`),
  createProject: (payload: ProjectDraft) =>
    request<ResearchProject>('/projects', { method: 'POST', body: JSON.stringify(payload) }),
  updateProject: (projectId: ProjectId, payload: Partial<ProjectDraft>) =>
    request<ResearchProject>(`/projects/${encodeId(projectId)}`, {
      method: 'PATCH', body: JSON.stringify(payload),
    }),
  deleteProject: (projectId: ProjectId) => request<void>(`/projects/${encodeId(projectId)}`, { method: 'DELETE' }),
  addProjectPaper: async (projectId: ProjectId, paperId: PaperId, payload: MembershipPayload = {}) => {
    const body = withMembershipDefaults(payload)
    return request<ProjectMembership>(
      `/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  updateProjectPaper: (projectId: ProjectId, paperId: PaperId, payload: MembershipPayload) =>
    request<ProjectMembership>(
      `/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    ),
  removeProjectPaper: (projectId: ProjectId, paperId: PaperId) =>
    request<void>(`/projects/${encodeId(projectId)}/papers/${encodeId(paperId)}`, { method: 'DELETE' }),
  dailyReport: (date: string) => request<DailyReport>(`/reports/daily?date=${encodeURIComponent(date)}`),
  dailyReportHistory: async (limit = 90) => (
    await request<{ items: DailyReportArchiveItem[]; count: number }>(`/reports/daily/history?limit=${encodeURIComponent(String(limit))}`)
  ).items,
  regenerateDailyReport: (date: string) =>
    request<DailyReport>(`/reports/daily/regenerate?date=${encodeURIComponent(date)}`, { method: 'POST' }),
  synthesizeDailyReport: (date: string, force = true) =>
    request<DailyReport>(`/reports/daily/synthesize?date=${encodeURIComponent(date)}&force=${force ? 'true' : 'false'}`, { method: 'POST' }),
  sendDailyReport: (date: string) => request<{ ok: boolean; email_sent: number; email_failed: number; email_status: string }>(`/reports/daily/send?date=${encodeURIComponent(date)}`, { method: 'POST' }),
}

type PaperId = number | string
type ProjectId = number | string

function encodeId(id: PaperId | ProjectId) {
  return encodeURIComponent(String(id))
}

function withMembershipDefaults(payload: MembershipPayload) {
  return {
    reading_state: payload.reading_state ?? 'queued',
    priority: payload.priority ?? 3,
    notes: payload.notes ?? '',
    tags: payload.tags ?? [],
  }
}

function interestsUpdatePayload(interests: Interests) {
  return {
    topics: interests.topics.map((topic) => ({
      ...(topic.id !== undefined ? { id: topic.id } : {}),
      name: topic.name.trim(),
      description: topic.description.trim(),
      aliases: unique(topic.aliases ?? []),
      arxiv_categories: unique(topic.arxiv_categories ?? []),
      keywords: unique(topic.keywords ?? []),
      enabled: topic.enabled !== false,
    })),
    watched_authors: unique(interests.watched_authors),
    watched_organizations: unique(interests.watched_organizations),
  }
}

function settingsUpdatePayload(settings: Settings & { api_keys?: Partial<Record<ModelStage, string>>; smtp_password?: string }) {
  return {
    providers: Object.fromEntries(Object.entries(settings.providers).map(([stage, provider]) => [stage, {
      model_source: provider.model_source,
      provider_name: provider.provider_name,
      model_name: provider.model_name,
      api_base_url: provider.api_base_url,
      api_protocol: provider.api_protocol,
      codex_model: provider.codex_model,
      codex_reasoning_effort: provider.codex_reasoning_effort,
      ...(settings.api_keys?.[stage as ModelStage] ? { api_key: settings.api_keys[stage as ModelStage] } : {}),
    }])),
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
    deep_analysis_prompt: settings.deep_analysis_prompt,
    email_enabled: settings.email_enabled,
    email_recipients: settings.email_recipients,
    smtp_host: settings.smtp_host,
    smtp_port: settings.smtp_port,
    smtp_security: settings.smtp_security,
    smtp_username: settings.smtp_username,
    smtp_sender: settings.smtp_sender,
    public_url: settings.public_url,
    ...(settings.smtp_password ? { smtp_password: settings.smtp_password } : {}),
  }
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)))
}
