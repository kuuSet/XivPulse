import type { Paper } from './papers'

interface DailyNotablePaper {
  paper_id: Paper['id']
  arxiv_id: string
  title: string
  authors: string[]
  notable_organizations: string[]
  published_at: string
  score: number
  quality_score: number | null
  summary: string
  recommendation: string
  contributions: string[]
  methods: string[]
  evidence: string[]
  analysis_available: boolean
}

export interface DailyAggregateItem {
  name: string
  count: number
  paper_ids: Array<Paper['id']>
  shared: boolean
}

interface DailyReadingQueueItem {
  rank: number
  paper_id: Paper['id']
  arxiv_id: string
  title: string
  priority: 'must_read' | 'read' | 'skim' | 'skip'
  score: number
  estimated_minutes: number
  next_action: string
}

interface DailyAiSynthesis {
  status: 'ready' | 'not_configured' | 'insufficient_data' | 'disabled' | string
  headline?: string
  overall_assessment?: string
  core_threads?: Array<{
    title: string
    analysis: string
    paper_ids: Array<Paper['id']>
  }>
  method_comparison?: string
  evidence_assessment?: string
  disagreements_and_risks?: string
  research_outlook?: string
  recommended_actions?: string[]
  generated_at?: string
  provider?: string
  model?: string
  transport?: string
  budget_mode?: string
  source_hash?: string
  stale?: boolean
  usage?: {
    available: boolean
    input_tokens: number | null
    cached_input_tokens: number | null
    output_tokens: number | null
    reasoning_tokens: number | null
    total_tokens: number | null
  }
}

export interface DailyReport {
  snapshot_id?: number
  report_date: string
  title: string
  timezone: string
  date_basis: string
  persisted?: boolean
  generated_at?: string
  updated_at?: string
  coverage: {
    fetched?: number
    prefiltered?: number
    prefilter_kept?: number
    screened?: number
    deep_analyzed?: number
    paper_count: number
    analyzed_paper_count: number
    deep_analysis_count: number
    analysis_coverage_percent: number
    deep_analysis_coverage_percent: number
    high_score_paper_count: number
    average_score: number | null
  }
  executive_summary: string
  ai_synthesis?: DailyAiSynthesis
  research_situation: {
    synthesis: string
    volume: {
      today: number
      previous_day: number
      change: number
      recent_daily_average: number
      status: 'above_baseline' | 'near_baseline' | 'below_baseline'
      assessment: string
    }
    score_summary: {
      average: number | null
      previous_average: number | null
      change: number | null
      highest: number | null
      high_score_count: number
      high_score_threshold: number
    }
    method_signals: string[]
    evidence_maturity: {
      status: 'no_signal' | 'insufficient_analysis' | 'preliminary' | 'mixed' | 'strong'
      assessment: string
      strong_signal_count: number
      shared_strong_signals: string[]
      deep_analysis_coverage_percent: number
    }
    quality_overview: {
      score_threshold: number
      qualified_paper_count: number
      included_paper_count: number
      analyzed_paper_count: number
      deep_analysis_count: number
      lead: string
      score_range: {
        lowest: number
        highest: number
        average: number
      } | null
      paper_findings: Array<{
        paper_id: Paper['id']
        arxiv_id: string
        title: string
        score: number
        contribution: string
        method: string
        evidence: string
        limitation: string
        analysis_kind: string | null
      }>
      comparison: string
      evidence_conclusion: string
    }
  }
  highlight_papers: DailyNotablePaper[]
  common_methods: DailyAggregateItem[]
  common_evidence: DailyAggregateItem[]
  suggested_reading_queue: DailyReadingQueueItem[]
  methodology_note: string
}

export interface DailyReportArchiveItem {
  id: number
  report_date: string
  title: string
  generated_at: string
  updated_at?: string
  coverage?: DailyReport['coverage']
}
