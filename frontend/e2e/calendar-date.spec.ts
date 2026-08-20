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
        prefiltered_papers: 1,
        prefilter_kept_papers: 1,
        screened_papers: 1,
        high_priority_papers: 1,
        items: [paper(day)],
        topic_stats: [],
        interests: { watched_authors: [], watched_organizations: [] },
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
          prefiltered: 1,
          prefilter_kept: 1,
          screened: 1,
          pending_prefilter: 0,
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

  await expect(page.locator('.calendar-grid')).toHaveCount(0)
  await page.getByRole('button', { name: '展开日期日历' }).click()
  await page.locator('.calendar-day strong').getByText('13', { exact: true }).click()
  await expect(titles).toHaveText(['Paper for August 13'])
  await expect(page).toHaveURL(/date=2026-08-13/)
  expect(requestedDates).toContain('2026-08-13')

  await page.getByRole('button', { name: '展开日期日历' }).click()
  await page.locator('.calendar-day strong').getByText('14', { exact: true }).click()
  await expect(titles).toHaveText(['Paper for August 14'])
  await expect(page).toHaveURL(/date=2026-08-14/)
  expect(requestedDates).toContain('2026-08-14')

  await page.goBack()
  await expect(titles).toHaveText(['Paper for August 13'])
  await page.reload()
  await expect(titles).toHaveText(['Paper for August 13'])
})

test('opening a paper preserves the workspace position and resets only the reader', async ({ page }) => {
  const paper = (id: number) => ({
    id,
    arxiv_id: `2608.2000${id}`,
    title: `Scroll test paper ${id}`,
    authors: ['Test Author'],
    affiliations: [],
    categories: ['cs.AI'],
    abstract: `Long abstract for paper ${id}. `.repeat(300),
    submitted_at: '2026-08-14T12:00:00Z',
    abs_url: `https://arxiv.org/abs/2608.2000${id}`,
    pdf_url: `https://arxiv.org/pdf/2608.2000${id}`,
    effective_score: 90 - id,
    match_reasons: ['Test topic'],
  })
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-14',
        total_papers: 2,
        prefiltered_papers: 2,
        prefilter_kept_papers: 2,
        screened_papers: 2,
        high_priority_papers: 2,
        items: [paper(1), paper(2)],
        topic_stats: [],
        interests: { watched_authors: [], watched_organizations: [] },
      }),
    })
  })
  await page.route('**/api/job-calendar*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ month: '2026-08', latest_available_date: '2026-08-16', days: [] }),
    })
  })

  await page.goto('/?view=today')
  await page.locator('.paper-row').first().click()
  await expect(page.locator('.detail-column')).toHaveClass(/mobile-visible/)
  const workbench = page.locator('.paper-workbench')
  await workbench.scrollIntoViewIfNeeded()
  const windowScroll = await page.evaluate(() => window.scrollY)
  const reader = page.locator('.detail-scroll')
  await reader.evaluate((element) => { element.scrollTop = element.scrollHeight })
  expect(await reader.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)

  await page.locator('.paper-row').nth(1).click()
  await expect(page).toHaveURL(/paper=2/)
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeCloseTo(windowScroll, 0)
  expect(await reader.evaluate((element) => element.scrollTop)).toBe(0)
})
