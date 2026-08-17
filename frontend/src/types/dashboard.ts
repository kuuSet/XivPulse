import type { Paper } from './papers'
import type { Interests } from './interests'
import type { Settings } from './settings'
import type { JobInfo, TopicStat } from './system'

export interface DashboardData {
  date: string
  total_papers: number
  semantic_candidate_papers: number
  screened_papers: number
  high_priority_papers: number
  processing_summary?: {
    latest_update_at?: string | null
    latest_fetched_count: number
    latest_screened_count: number
    latest_deep_analyzed_count: number
    pending_screening_count: number
    pending_deep_count: number
    screening_limit: number
    screening_semantic_threshold: number
    screening_queue_total?: number
    screening_completed?: number
    screening_failed?: number
    screening_current_batch?: number
    screening_total_batches?: number
    report_incomplete?: boolean
    ai_configured: boolean
    deep_analysis_limit: number
    deep_analysis_threshold: number
  }
  items: Paper[]
  interests?: Interests
  topic_stats: TopicStat[]
  settings?: Settings
  active_job?: JobInfo | null
  latest_digest?: {
    id: number
    digest_date: string
    title: string
    status: string
  } | null
}
