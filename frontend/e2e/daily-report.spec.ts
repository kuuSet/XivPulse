import { expect, test } from '@playwright/test'
import { mockAppShell } from './support'

test('daily report opens the latest saved date without treating it as a completed run', async ({ page }) => {
  await mockAppShell(page)
  const requestedDates: Array<string | null> = []
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
        interests: { topics: [], watched_authors: [], watched_organizations: [] },
        latest_report_date: '2026-08-13',
        latest_completed_date: '2026-08-12',
      }),
    })
  })
  await page.route('**/api/reports/daily**', async (route) => {
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
        timezone: 'Asia/Shanghai',
        date_basis: 'arxiv_submission_batch',
        persisted: true,
        executive_summary: 'Saved independently from the last completed daily run.',
        coverage: {
          paper_count: 0,
          analyzed_paper_count: 0,
          deep_analysis_count: 0,
          analysis_coverage_percent: 0,
          deep_analysis_coverage_percent: 0,
          high_score_paper_count: 0,
          average_score: null,
        },
        research_situation: {
          synthesis: 'No papers were screened for this date.',
          volume: { today: 0, previous_day: 0, change: 0, recent_daily_average: 0, status: 'near_baseline', assessment: 'No activity.' },
          score_summary: { average: null, previous_average: null, change: null, highest: null, high_score_count: 0, high_score_threshold: 80 },
          method_signals: [],
          evidence_maturity: { status: 'no_signal', assessment: 'No evidence.', strong_signal_count: 0, shared_strong_signals: [], deep_analysis_coverage_percent: 0 },
          quality_overview: {
            score_threshold: 80,
            qualified_paper_count: 0,
            included_paper_count: 0,
            analyzed_paper_count: 0,
            deep_analysis_count: 0,
            score_range: null,
            lead: 'No high-scoring papers.',
            paper_findings: [],
            comparison: 'No comparison available.',
            evidence_conclusion: 'No evidence available.',
          },
        },
        highlight_papers: [],
        common_methods: [],
        common_evidence: [],
        suggested_reading_queue: [],
        methodology_note: 'Test report.',
      }),
    })
  })

  await page.goto('/?view=digest')

  await expect(page.getByRole('heading', { name: 'Latest saved report' })).toBeVisible()
  await expect(page.locator('.report-archive-list')).toContainText('待日更')
  await expect.poll(() => requestedDates).toContain('2026-08-13')
})
