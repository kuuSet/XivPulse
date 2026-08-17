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
