import { expect, test } from '@playwright/test'

test('today papers can prioritize watched authors or organizations', async ({ page }) => {
  const paper = (id: number, title: string, authors: string[], affiliations: string[], score: number) => ({
    id,
    arxiv_id: `2608.5000${id}`,
    title,
    authors,
    affiliations,
    categories: ['cs.AI'],
    abstract: 'Test abstract.',
    submitted_at: '2026-08-16T12:00:00Z',
    abs_url: `https://arxiv.org/abs/2608.5000${id}`,
    pdf_url: `https://arxiv.org/pdf/2608.5000${id}`,
    effective_score: score,
    score_source: 'ai',
    match_reasons: ['Test topic'],
  })
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-16',
        total_papers: 3,
        semantic_candidate_papers: 3,
        screened_papers: 3,
        high_priority_papers: 1,
        items: [
          paper(1, 'Highest relevance', ['John Smith'], ['Other Lab'], 95),
          paper(2, 'Watched author paper', ['Jane Doe'], ['Other Lab'], 75),
          paper(3, 'Watched organization paper', ['John Smith'], ['NVIDIA Research'], 70),
        ],
        topic_stats: [],
        interests: {
          watched_authors: ['Jane Doe'],
          watched_organizations: ['NVIDIA'],
        },
      }),
    })
  })
  await page.goto('/?view=today')
  await page.getByLabel('论文范围').selectOption('all')
  const titles = page.locator('.paper-list .paper-row h2')

  await expect(titles.first()).toHaveText('Highest relevance')
  await page.getByLabel('排序').selectOption('watched_authors')
  await expect(titles.first()).toHaveText('Watched author paper')
  await page.getByLabel('排序').selectOption('watched_organizations')
  await expect(titles.first()).toHaveText('Watched organization paper')
})

test('semantic candidates stay visible without an AI screening score', async ({ page }) => {
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-16',
        total_papers: 1,
        semantic_candidate_papers: 1,
        screened_papers: 0,
        high_priority_papers: 0,
        processing_summary: {
          latest_fetched_count: 1,
          latest_screened_count: 0,
          latest_deep_analyzed_count: 0,
          pending_screening_count: 0,
          pending_deep_count: 0,
          screening_limit: 30,
          screening_semantic_threshold: 0.66,
          deep_analysis_limit: 5,
          deep_analysis_threshold: 90,
          ai_configured: false,
        },
        items: [{
          id: 1,
          arxiv_id: '2608.60001',
          title: 'Semantic-only paper',
          authors: ['Test Author'],
          categories: ['cs.AI'],
          abstract: 'Test abstract.',
          submitted_at: '2026-08-16T12:00:00Z',
          abs_url: 'https://arxiv.org/abs/2608.60001',
          pdf_url: 'https://arxiv.org/pdf/2608.60001',
          semantic_score: 0.75,
          screening_relevance_score: null,
          effective_score: 75,
          score_source: 'semantic',
          match_reasons: ['Test topic'],
        }],
        topic_stats: [],
        interests: { watched_authors: [], watched_organizations: [] },
      }),
    })
  })

  await page.goto('/?view=today')

  await expect(page.locator('.paper-list h2')).toHaveText(['Semantic-only paper'])
  await expect(page.locator('.score-pill')).toContainText('75')
  await expect(page.locator('.score-pill')).toContainText('语义相似')
  await expect(page.getByText('未配置 API Key，已跳过')).toBeVisible()
})
