import { expect, test } from '@playwright/test'

test('daily report opens the latest saved date without treating it as a completed run', async ({ page }) => {
  const requestedDates: Array<string | null> = []

  await page.route('**/api/auth/session', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: false, authenticated: true }) })
  })
  await page.route('**/api/projects', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], count: 0 }) })
  })
  await page.route('**/api/status', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ busy: false, active_job: null, latest_job: null }) })
  })
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: '2026-08-14',
        total_papers: 0,
        prefiltered_papers: 0,
        prefilter_kept_papers: 0,
        screened_papers: 0,
        high_priority_papers: 0,
        items: [],
        topic_stats: [],
        interests: { topics: [], watched_authors: [], watched_organizations: [] },
        latest_report_date: '2026-08-13',
        latest_completed_date: '2026-08-12',
      }),
    })
  })
  await page.route('**/api/reports/daily*', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/history')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [{ id: 13, report_date: '2026-08-13', title: 'Latest saved report', generated_at: '2026-08-13T14:00:00Z', coverage: { paper_count: 0, analyzed_paper_count: 0 } }],
          count: 1,
        }),
      })
      return
    }
    requestedDates.push(url.searchParams.get('date'))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        snapshot_id: 13,
        report_date: '2026-08-13',
        title: 'Latest saved report',
        persisted: true,
        executive_summary: 'Saved independently from the last completed daily run.',
        coverage: { paper_count: 0, analyzed_paper_count: 0, deep_analysis_count: 0 },
        topic_insights: [],
        highlight_papers: [],
        common_methods: [],
        common_evidence: [],
        limitations_and_research_gaps: [],
        suggested_reading_queue: [],
      }),
    })
  })

  await page.goto('/?view=digest')

  await expect(page.getByRole('heading', { name: 'Latest saved report' })).toBeVisible()
  await expect(page.locator('.report-archive-list')).toContainText('待日更')
  await expect.poll(() => requestedDates).toContain('2026-08-13')
})
