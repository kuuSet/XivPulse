export interface BackendTopic {
  id?: number
  name: string
  description: string
  aliases: string[]
  arxiv_categories: string[]
  keywords: string[]
  enabled?: boolean
}

export interface DirectionCatalogItem extends BackendTopic {
  context_keywords: string[]
  slug: string
  group: string
  level: 'main' | 'specialty'
  parent_slug?: string | null
  parent_name?: string | null
}

interface DirectionCatalogSource {
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

export interface Interests {
  topics: BackendTopic[]
  watched_authors: string[]
  watched_organizations: string[]
}
