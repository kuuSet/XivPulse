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
  watched_authors?: string[]
  min_relevance?: number
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
  watched_authors: string[]
  min_relevance: number
}
