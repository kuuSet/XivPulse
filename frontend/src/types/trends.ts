import type { Paper } from './papers'

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

interface TopicTrendPeriod {
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

type RoadmapNodeStatus = 'emerging' | 'active' | 'established'

interface RoadmapNode {
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

interface RoadmapRelation {
  source: string
  target: string
  type?: string
  directed?: boolean
  weight: number
  strength_percent?: number
  paper_ids?: Array<Paper['id']>
}

interface RoadmapCluster {
  id: string
  label: string
  description?: string
  node_ids: string[]
  color?: string
}

export interface ResearchRoadmap {
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
