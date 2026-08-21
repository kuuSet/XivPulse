import type { Paper } from './papers'
import type { RepresentativePaper, TrendDirection } from './trends'

interface DailyTopicInsight {
  topic_id?: string
  topic: string
  paper_count: number
  direction?: TrendDirection
  average_score?: number | null
  insight: string
  representative_papers?: RepresentativePaper[]
}

interface DailyNotablePaper {
  paper_id: Paper['id']
  arxiv_id?: string
  title: string
  score?: number
  summary?: string
  why_it_matters?: string
  recommendation?: string
  strengths?: string[]
  limitations?: string[]
  notable_organizations?: string[]
}

export interface DailyAggregateItem {
  name?: string
  item?: string
  count: number
  paper_ids?: Array<Paper['id']>
  papers?: RepresentativePaper[]
}

export interface DailyResearchGap {
  text: string
  count?: number
  basis?: string
  paper_ids?: Array<Paper['id']>
  papers?: RepresentativePaper[]
}

interface DailyReadingQueueItem {
  rank: number
  paper_id: Paper['id']
  arxiv_id?: string
  title: string
  priority: 'must_read' | 'read' | 'skim' | 'skip' | string
  score?: number
  estimated_minutes?: number
  next_action?: string
  reason?: string
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
  id?: number
  snapshot_id?: number
  report_date: string
  title?: string
  timezone?: string
  persisted?: boolean
  generated_at?: string
  updated_at?: string
  version?: number
  coverage?: {
    fetched?: number
    prefiltered?: number
    prefilter_kept?: number
    screened?: number
    deep_analyzed?: number
    paper_count: number
    candidate_paper_count?: number
    unattributed_candidate_count?: number
    direction_attributed_paper_count?: number
    relevant_paper_count?: number
    direction_attribution_coverage_percent?: number
    analyzed_paper_count?: number
    deep_analysis_count?: number
    analysis_coverage_percent?: number
    deep_analysis_coverage_percent?: number
    high_score_paper_count?: number
  }
  executive_summary: string
  ai_synthesis?: DailyAiSynthesis
  research_situation?: {
    synthesis: string
    volume: {
      today: number
      previous_day: number
      change: number
      recent_daily_average: number
      status: 'above_baseline' | 'near_baseline' | 'below_baseline'
      assessment: string
    }
    direction_dynamics: {
      active: string[]
      rising: string[]
      falling: string[]
      top_direction_share_percent: number
      concentration: 'high' | 'medium' | 'distributed'
    }
    method_signals: string[]
    evidence_maturity: {
      status: 'no_signal' | 'insufficient_analysis' | 'preliminary' | 'mixed' | 'strong'
      assessment: string
      strong_signal_count: number
      shared_strong_signals?: string[]
      deep_analysis_coverage_percent?: number
    }
    quality_overview?: {
      score_threshold: number
      qualified_paper_count: number
      included_paper_count: number
      analyzed_paper_count: number
      deep_analysis_count: number
      lead: string
      main_threads: Array<{
        name: string
        paper_count: number
        average_score?: number | null
        paper_ids: Array<Paper['id']>
      }>
      paper_findings: Array<{
        paper_id: Paper['id']
        arxiv_id: string
        title: string
        score: number
        topics: string[]
        contribution: string
        method: string
        evidence: string
        limitation: string
        analysis_kind?: string | null
      }>
      comparison: string
      evidence_conclusion: string
    }
  }
  topic_insights: DailyTopicInsight[]
  highlight_papers: DailyNotablePaper[]
  common_methods: DailyAggregateItem[]
  common_evidence: DailyAggregateItem[]
  limitations_and_research_gaps: Array<string | DailyResearchGap>
  suggested_reading_queue: DailyReadingQueueItem[]
  methodology_note?: string
}

export interface DailyReportArchiveItem {
  id: number
  report_date: string
  title: string
  generated_at: string
  updated_at?: string
  coverage?: DailyReport['coverage']
}
