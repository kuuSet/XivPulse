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
