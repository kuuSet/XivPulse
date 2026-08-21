import type { Paper } from './papers'
import type { Interests } from './interests'
import type { Settings } from './settings'
import type { JobInfo } from './system'

export interface DashboardData {
  date: string
  total_papers: number
  prefiltered_papers: number
  prefilter_kept_papers: number
  screened_papers: number
  high_priority_papers: number
  processing_summary?: {
    latest_update_at?: string | null
    latest_fetched_count: number
    latest_screened_count: number
    latest_deep_analyzed_count: number
    pending_prefilter_count: number
    pending_screening_count: number
    pending_deep_count: number
    prefilter_batch_size: number
    prefiltered_count: number
    prefilter_kept_count: number
    screened_count: number
    ai_configured: boolean
    deep_analysis_limit: number
    deep_analysis_threshold: number
  }
  items: Paper[]
  interests?: Interests
  settings?: Settings
  active_job?: JobInfo | null
  latest_report_date?: string | null
  latest_completed_date?: string | null
}
