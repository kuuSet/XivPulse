export interface BackendTopic {
  id?: number
  name: string
  description: string
  aliases: string[]
  arxiv_categories: string[]
  enabled?: boolean
}

export interface InterestPreset {
  slug: string
  group: string
  name: string
  description: string
  aliases: string[]
  arxiv_categories: string[]
}

export interface InterestPresets {
  version: string
  items: InterestPreset[]
}

export interface Interests {
  topics: BackendTopic[]
  watched_authors: string[]
  watched_organizations: string[]
}
