export interface TopicStat {
  topic: string
  count: number
}

type JobState = 'queued' | 'running' | 'completed' | 'failed' | string

export interface JobInfo {
  id: string
  kind: string
  status: JobState
  progress: number
  message: string
  error?: string | null
  result?: Record<string, unknown>
  created_at: string
  started_at?: string | null
  completed_at?: string | null
}

export interface AppStatus {
  busy: boolean
  active_job?: JobInfo | null
  latest_job?: JobInfo | null
}

interface JobCalendarDay {
  date: string
  status: 'completed' | 'partial'
  fetched: number
  prefiltered: number
  prefilter_kept: number
  screened: number
  pending_prefilter: number
  pending_screening: number
  analyzed: number
  message: string
}

export interface JobCalendarData {
  month: string
  latest_available_date: string
  days: JobCalendarDay[]
}

export interface AuthSession {
  enabled: boolean
  authenticated: boolean
  username?: string | null
}
