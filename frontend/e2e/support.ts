import type { Page, Route } from '@playwright/test'
import type { DashboardData } from '../src/types/dashboard'
import type { DirectionCatalog, Interests } from '../src/types/interests'

const interests = {
  topics: [{
    id: 1,
    name: 'Embodied AI Systems',
    description: 'Robotic systems that connect perception, reasoning, planning, and physical action.',
    aliases: ['Embodied AI'],
    arxiv_categories: ['cs.RO', 'cs.AI'],
    keywords: ['robot learning'],
    enabled: true,
  }],
  watched_authors: [],
  watched_organizations: [],
} satisfies Interests

const directionCatalog = {
  version: 'test',
  updated_at: '2026-08-21',
  groups: ['Systems'],
  count: 2,
  items: [
    {
      slug: 'embodied-ai',
      group: 'Systems',
      level: 'main',
      name: 'Embodied AI Systems',
      description: 'Embodied agents and robots.',
      aliases: ['Embodied AI'],
      arxiv_categories: ['cs.RO', 'cs.AI'],
      keywords: ['robot learning'],
      context_keywords: ['perception', 'control'],
      enabled: true,
    },
    {
      slug: 'llm-systems',
      group: 'Systems',
      level: 'specialty',
      parent_slug: null,
      parent_name: null,
      name: 'Large Language Model Systems',
      description: 'Training and serving infrastructure for language models.',
      aliases: ['LLM Systems'],
      arxiv_categories: ['cs.CL', 'cs.DC'],
      keywords: ['model serving'],
      context_keywords: ['inference', 'distributed training'],
      enabled: true,
    },
  ],
  sources: [],
  scope_note: 'Deterministic Playwright catalog.',
} satisfies DirectionCatalog

const dashboard = {
  date: '2026-08-21',
  total_papers: 0,
  prefiltered_papers: 0,
  prefilter_kept_papers: 0,
  screened_papers: 0,
  high_priority_papers: 0,
  items: [],
  interests,
  latest_report_date: null,
  latest_completed_date: null,
} satisfies DashboardData

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

export async function mockAppShell(page: Page) {
  await page.route('**/api/**', (route) => {
    throw new Error(`Unmocked API: ${route.request().method()} ${route.request().url()}`)
  })
  await page.route('**/api/auth/session', (route) => json(route, { enabled: false, authenticated: true }))
  await page.route('**/api/projects', (route) => json(route, { items: [], count: 0 }))
  await page.route('**/api/status', (route) => json(route, { busy: false, active_job: null, latest_job: null }))
  await page.route('**/api/dashboard*', (route) => json(route, dashboard))
  await page.route('**/api/job-calendar*', (route) => json(route, {
    month: '2026-08', latest_available_date: '2026-08-21', days: [],
  }))
  await page.route('**/api/interests', (route) => json(route, interests))
  await page.route('**/api/directions/catalog', (route) => json(route, directionCatalog))
}
