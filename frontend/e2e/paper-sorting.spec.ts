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
    match_reasons: ['Test topic'],
  })
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-16',
        total_papers: 3,
        prefiltered_papers: 3,
        prefilter_kept_papers: 3,
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
  const titles = page.locator('.paper-list .paper-row h2')

  await expect(page.getByLabel('论文范围')).toHaveValue('all')
  await expect(titles).toHaveCount(3)
  await expect(titles.first()).toHaveText('Highest relevance')
  await page.getByLabel('排序').selectOption('watched_authors')
  await expect(titles.first()).toHaveText('Watched author paper')
  await page.getByLabel('排序').selectOption('watched_organizations')
  await expect(titles.first()).toHaveText('Watched organization paper')
})

test('abstract-scored papers show the LLM relevance score', async ({ page }) => {
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-16',
        total_papers: 1,
        prefiltered_papers: 1,
        prefilter_kept_papers: 1,
        screened_papers: 1,
        high_priority_papers: 0,
        processing_summary: {
          latest_fetched_count: 1,
          latest_screened_count: 1,
          latest_deep_analyzed_count: 0,
          pending_prefilter_count: 0,
          pending_screening_count: 0,
          pending_deep_count: 0,
          prefilter_batch_size: 30,
          prefiltered_count: 1,
          prefilter_kept_count: 1,
          screened_count: 1,
          deep_analysis_limit: 5,
          deep_analysis_threshold: 90,
          ai_configured: true,
        },
        items: [{
          id: 1,
          arxiv_id: '2608.60001',
          title: 'Abstract-scored paper',
          authors: ['Test Author'],
          categories: ['cs.AI'],
          abstract: 'Test abstract.',
          submitted_at: '2026-08-16T12:00:00Z',
          abs_url: 'https://arxiv.org/abs/2608.60001',
          pdf_url: 'https://arxiv.org/pdf/2608.60001',
          screening_relevance_score: 75,
          effective_score: 75,
          match_reasons: ['Test topic'],
        }],
        topic_stats: [],
        interests: { watched_authors: [], watched_organizations: [] },
      }),
    })
  })

  await page.goto('/?view=today')
  await page.getByLabel('论文范围').selectOption('all')

  await expect(page.locator('.paper-list h2')).toHaveText(['Abstract-scored paper'])
  await expect(page.locator('.score-pill')).toContainText('75')
  await expect(page.locator('.score-pill')).toContainText('粗排分')
})
