import { expect, test } from '@playwright/test'

test('calendar dates load only papers from the selected day', async ({ page }) => {
  const requestedDates: Array<string | null> = []
  const paper = (day: string) => ({
    id: Number(day),
    arxiv_id: `2608.100${day}`,
    title: `Paper for August ${day}`,
    authors: ['Test Author'],
    affiliations: [],
    categories: ['cs.AI'],
    abstract: 'Test abstract.',
    submitted_at: `2026-08-${day}T12:00:00Z`,
    abs_url: `https://arxiv.org/abs/2608.100${day}`,
    pdf_url: `https://arxiv.org/pdf/2608.100${day}`,
    effective_score: 90,
    score_source: 'ai',
    match_reasons: ['Test topic'],
  })
  await page.route('**/api/dashboard*', async (route) => {
    const date = new URL(route.request().url()).searchParams.get('date')
    requestedDates.push(date)
    const day = date?.slice(-2) ?? '14'
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: date ?? '2026-08-14',
        total_papers: 1,
        semantic_candidate_papers: 1,
        screened_papers: 1,
        high_priority_papers: 1,
        items: [paper(day)],
        topic_stats: [],
        profile: { watched_authors: [], watched_organizations: [] },
      }),
    })
  })
  await page.route('**/api/job-calendar*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        month: '2026-08',
        latest_available_date: '2026-08-16',
        days: ['13', '14'].map((day) => ({
          date: `2026-08-${day}`,
          status: 'completed',
          fetched: 1,
          candidates: 1,
          screened: 1,
          pending_screening: 0,
          analyzed: 0,
          message: '任务完整完成',
        })),
      }),
    })
  })

  await page.goto('/?view=today')
  const titles = page.locator('.paper-list h2')
  await expect(titles).toHaveText(['Paper for August 14'])

  await page.locator('.calendar-day strong').getByText('13', { exact: true }).click()
  await expect(titles).toHaveText(['Paper for August 13'])
  expect(requestedDates).toContain('2026-08-13')

  await page.locator('.calendar-day strong').getByText('14', { exact: true }).click()
  await expect(titles).toHaveText(['Paper for August 14'])
  expect(requestedDates).toContain('2026-08-14')
})
