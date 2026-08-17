import type { Paper, ReadingState } from './papers'

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

export type ProjectDraft = Pick<ResearchProject, 'name' | 'description' | 'status' | 'color' | 'goal'> & {
  due_date?: string | null
}
