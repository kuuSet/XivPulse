export type FeedbackAction = 'save' | 'dismiss' | 'like' | 'dislike'
export type FeedbackWriteAction = FeedbackAction | 'neutral'
export type FeedbackReason =
  | 'not_relevant'
  | 'incremental'
  | 'weak_evidence'
  | 'no_real_robot'
  | 'no_code'
  | 'duplicate'
  | 'poor_writing'
  | 'topic_pause'

export type ReadingState = 'unread' | 'queued' | 'reading' | 'read' | 'reproduce' | 'cited' | 'archived'

export interface ReadingData {
  state: ReadingState
  priority: number
  notes: string
  tags: string[]
  created_at?: string | null
  updated_at?: string | null
}

export interface BackendTopic {
  id?: number
  name: string
  aliases: string[]
  query?: string
  arxiv_categories: string[]
  keywords: string[]
  context_keywords: string[]
  negative_keywords: string[]
  weight?: number
  enabled?: boolean
}

export interface DirectionCatalogItem extends BackendTopic {
  slug: string
  group: string
  description: string
  level: 'main' | 'specialty'
  parent_slug?: string | null
  parent_name?: string | null
}

export interface DirectionCatalogSource {
  name: string
  url: string
  role: string
  retrieved_at: string
}

export interface DirectionCatalog {
  version: string
  updated_at: string
  groups: string[]
  count: number
  main_count?: number
  specialty_count?: number
  items: DirectionCatalogItem[]
  sources: DirectionCatalogSource[]
  scope_note: string
}

export interface BackendProfile {
  id?: number
  name: string
  description: string
  language?: string
  active?: boolean
  topics: BackendTopic[]
  positive_keywords?: string[]
  negative_keywords?: string[]
  categories?: string[]
  author_watchlist?: string[]
  min_relevance?: number
}

export interface Feedback {
  action?: FeedbackWriteAction | 'skip' | null
  note?: string | null
  rating?: number | null
  reasons?: FeedbackReason[]
}

export interface PaperAnalysis {
  kind?: 'screening' | 'deep' | string
  overview?: string
  contributions?: string[]
  method?: string
  evidence?: string
  limitations?: string[]
  advocate?: string
  skeptic?: string
  input_scope?: string
  budget_mode?: 'economy' | 'balanced' | 'deep' | string
  token_usage?: {
    available: boolean
    input_tokens: number | null
    cached_input_tokens: number | null
    output_tokens: number | null
    reasoning_tokens: number | null
    total_tokens: number | null
  }
  relevance_score?: number
  novelty_score?: number
  method_score?: number
  evidence_score?: number
  potential_score?: number
  tags?: string[]
}

export interface Paper {
  id: number | string
  arxiv_id: string
  version?: string
  title: string
  authors: string[]
  affiliations?: string[]
  notable_organizations?: string[]
  categories: string[]
  abstract: string
  submitted_at: string
  updated_at?: string | null
  abs_url: string
  pdf_url: string
  html_url?: string | null
  relevance_score: number
  quality_score?: number | null
  novelty_score?: number | null
  priority?: string
  match_reasons: string[]
  matched_topics?: string[]
  semantic_score?: number | null
  semantic_candidate_topics?: Array<{ name: string; score: number }>
  matched_keywords?: string[]
  analysis_tags?: string[]
  summary?: string | null
  why_recommended?: string | null
  feedback?: Feedback | null
  feedback_reasons?: FeedbackReason[]
  reading?: ReadingData | null
  reading_state?: ReadingState
  reading_priority?: number
  notes?: string
  tags?: string[]
  project_memberships?: ProjectMembership[]
  projects?: ProjectMembership[]
  analysis?: PaperAnalysis | null
}

export type ProjectStatus = 'active' | 'paused' | 'completed'

export interface ProjectPaperSummary {
  id: Paper['id']
  arxiv_id: string
  title: string
  authors?: string[]
  primary_category?: string | null
  published_at?: string | null
}

export interface ProjectMembership {
  id?: number | string
  project_id: number | string
  paper_id: Paper['id']
  project_name?: string | null
  project_color?: string | null
  reading_state: ReadingState
  priority: number
  notes: string
  tags: string[]
  paper?: ProjectPaperSummary
  created_at?: string | null
  updated_at?: string | null
}

export interface ResearchProject {
  id: number | string
  name: string
  description: string
  kind?: 'custom' | 'favorites' | string
  is_system?: boolean
  status: ProjectStatus
  color: string
  goal: string
  due_date?: string | null
  paper_count: number
  papers?: ProjectMembership[]
  created_at?: string | null
  updated_at?: string | null
}

export interface PaperPage {
  items: Paper[]
  count: number
  total: number
  offset: number
  limit: number
  page: number
  pages: number
  has_previous: boolean
  has_next: boolean
  scope: 'screened'
  categories: string[]
}

export type ProjectDraft = Pick<ResearchProject, 'name' | 'description' | 'status' | 'color' | 'goal'> & {
  due_date?: string | null
}

export interface RepresentativePaper {
  paper_id: Paper['id']
  arxiv_id?: string
  title: string
  score?: number
}

export type TrendDirection = 'new' | 'rising' | 'stable' | 'falling'

export interface TopicTrendItem {
  id: string
  name: string
  sources?: string[]
  count: number
  previous_count: number
  change: number
  change_percent?: number | null
  direction: TrendDirection
  share_percent?: number
  average_score?: number | null
  average_quality?: number | null
  representative_papers?: RepresentativePaper[]
  series?: Array<{ period: string; count: number }>
}

export interface TopicTrendPeriod {
  granularity?: 'day' | 'week'
  start_date: string
  end_date: string
  previous_start_date?: string
  previous_end_date?: string
  paper_count: number
  previous_paper_count?: number
  candidate_paper_count?: number
  previous_candidate_paper_count?: number
  topics: TopicTrendItem[]
}

export interface TopicTrends {
  profile_id?: number | string
  as_of: string
  requested_as_of?: string
  latest_complete_date?: string
  awaiting_daily_update?: boolean
  timezone?: string
  date_basis?: string
  daily?: TopicTrendPeriod
  weekly?: TopicTrendPeriod
  topics?: TopicTrendItem[]
  periods?: string[]
}

export type RoadmapNodeStatus = 'emerging' | 'active' | 'established'

export interface RoadmapNode {
  id: string
  label: string
  sources?: string[]
  paper_count: number
  average_score?: number | null
  average_quality?: number | null
  first_seen?: string
  last_seen?: string
  status: RoadmapNodeStatus
  keywords?: string[]
  methods?: string[]
  representative_papers?: RepresentativePaper[]
  cluster_id?: string
}

export interface RoadmapRelation {
  source: string
  target: string
  type?: string
  directed?: boolean
  weight: number
  strength_percent?: number
  paper_ids?: Array<Paper['id']>
}

export interface RoadmapCluster {
  id: string
  label: string
  description?: string
  node_ids: string[]
  color?: string
}

export interface ResearchRoadmap {
  profile_id?: number | string
  as_of: string
  timezone?: string
  window?: { start_date: string; end_date: string; days: number }
  coverage?: { paper_count: number; candidate_paper_count?: number; direction_attributed_paper_count?: number; analyzed_paper_count?: number; node_count: number; relation_count: number }
  nodes: RoadmapNode[]
  relations: RoadmapRelation[]
  edges?: RoadmapRelation[]
  clusters?: RoadmapCluster[]
  timeline?: Array<{ start_date: string; end_date: string; paper_count: number; topic_ids: string[]; top_papers?: RepresentativePaper[] }>
  relation_basis?: string
}

export interface DailyTopicInsight {
  topic_id?: string
  topic: string
  paper_count: number
  direction?: TrendDirection
  average_score?: number | null
  insight: string
  representative_papers?: RepresentativePaper[]
}

export interface DailyNotablePaper {
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
  text?: string
  name?: string
  item?: string
  limitation?: string
  gap?: string
  count?: number
  basis?: string
  paper_ids?: Array<Paper['id']>
  papers?: RepresentativePaper[]
}

export interface DailyReadingQueueItem {
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

export interface DailyAiSynthesis {
  status: 'ready' | 'not_configured' | 'insufficient_data' | 'disabled' | 'error' | string
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
  last_error?: string
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
  incomplete?: boolean
  screening_status?: { total: number; completed: number; failed: number; batches: number }
  coverage?: {
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
    screening_queue_total?: number
    screening_completed?: number
    screening_failed?: number
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
  notable_papers?: DailyNotablePaper[]
  common_methods: DailyAggregateItem[]
  common_evidence: DailyAggregateItem[]
  shared_methods_evidence?: DailyAggregateItem[]
  limitations_and_research_gaps: Array<string | DailyResearchGap>
  limitations_research_gaps?: Array<string | DailyResearchGap>
  suggested_reading_queue: DailyReadingQueueItem[]
  recommended_reading_queue?: DailyReadingQueueItem[]
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

export interface AuthSession {
  enabled: boolean
  authenticated: boolean
  username?: string | null
}

export interface DashboardData {
  date: string
  total_papers: number
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
    deep_analysis_limit: number
    deep_relevance_threshold: number
  }
  items: Paper[]
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

export interface Profile {
  id?: number
  name: string
  description: string
  language?: string
  active?: boolean
  topics: BackendTopic[]
  positive_keywords: string[]
  negative_keywords: string[]
  categories: string[]
  author_watchlist: string[]
  min_relevance: number
}

export interface AnalysisPromptTemplate {
  id: string
  name: string
  description: string
  screening_prompt: string
  deep_prompt: string
}

export interface AnalysisPromptCatalog {
  version: string
  default_template_id: string
  max_prompt_chars: number
  templates: AnalysisPromptTemplate[]
  fixed_prompts: {
    screening: string
    deep: string
  }
}

export interface Settings {
  provider_name: string
  base_url: string
  model: string
  api_key_configured: boolean
  schedule_time: string
  timezone: string
  max_results: number
  screen_batch_size: number
  semantic_recall_mode: 'high' | 'balanced' | 'precise'
  semantic_recall_presets: Record<string, { semantic_threshold: number }>
  embedding: {
    model: string
    dimensions: number
    cache_dir: string
    loaded: boolean
    available: boolean
    message?: string
    backfill?: { status: string; processed?: number; total?: number; error?: string }
  }
  deep_analysis_top_n: number
  deep_relevance_threshold?: number
  retention_days: number
  analysis_budget_mode: 'economy' | 'balanced' | 'deep'
  daily_ai_synthesis_enabled: boolean
  analysis_prompt_template_id: string
  screening_prompt: string
  deep_prompt: string
  analysis_prompt_version: string
  analysis_prompt_customized: boolean
  token_usage: TokenUsageSummary
  automatic_cleanup_enabled: boolean
  database_path?: string | null
  database_size_bytes?: number | null
  stored_paper_count: number
  stored_analysis_count: number
  stored_report_count: number
  email_enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_security: 'starttls' | 'ssl'
  smtp_username: string
  smtp_password_configured: boolean
  smtp_from: string
  email_recipients: string[]
  public_base_url: string
  api_mode?: string
  mock_mode?: boolean
}

export interface TokenUsageTotals {
  record_count: number
  usage_available_count: number
  usage_missing_count: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
}

export interface TokenUsageSummary extends TokenUsageTotals {
  by_kind: Record<string, TokenUsageTotals>
  latest: null | {
    analysis_id: number
    kind: string
    budget_mode: string
    input_tokens: number | null
    cached_input_tokens: number | null
    output_tokens: number | null
    reasoning_tokens: number | null
    total_tokens: number | null
    created_at: string
  }
}

export interface MethodCatalogItem {
  slug: string
  group: string
  name: string
  aliases: string[]
  context_terms: string[]
  exclusions: string[]
  description: string
  source: 'built_in' | 'custom'
}

export interface MethodCatalog {
  version: string
  count: number
  built_in_count: number
  custom_count: number
  groups: string[]
  items: MethodCatalogItem[]
  scope_note: string
}

export interface MethodCandidate {
  key: string
  name: string
  paper_count: number
  paper_ids: number[]
  representative_papers: Array<{ paper_id: number; arxiv_id: string; title: string }>
  status: 'pending'
}

export interface MethodCandidates {
  window_days: number
  min_papers: number
  count: number
  items: MethodCandidate[]
  automatic_accept: false
  token_cost: 0
}
