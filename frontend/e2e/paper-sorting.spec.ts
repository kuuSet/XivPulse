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
    relevance_score: score,
    match_reasons: ['Test topic'],
  })
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-16',
        total_papers: 3,
        screened_papers: 3,
        high_priority_papers: 1,
        items: [
          paper(1, 'Highest relevance', ['John Smith'], ['Other Lab'], 95),
          paper(2, 'Watched author paper', ['Jane Doe'], ['Other Lab'], 75),
          paper(3, 'Watched organization paper', ['John Smith'], ['NVIDIA Research'], 70),
        ],
        topic_stats: [],
        profile: {
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
