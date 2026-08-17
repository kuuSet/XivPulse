import type { ProjectMembership } from './projects'

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
  screening_relevance_score?: number | null
  effective_score: number
  score_source: 'semantic' | 'ai'
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
  scope: 'candidates'
  categories: string[]
}
