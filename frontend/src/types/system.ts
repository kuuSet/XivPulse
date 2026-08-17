export interface TopicStat {
  topic: string
  count: number
}

export type JobState = 'queued' | 'running' | 'completed' | 'failed' | string

export interface JobInfo {
  id: string
  job_id?: string
  kind: string
  status: JobState
  progress: number
  total: number
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

export interface JobCalendarDay {
  date: string
  status: 'completed' | 'partial'
  fetched: number
  screened: number
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
