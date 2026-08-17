import { expect, test } from '@playwright/test'

test('research interests use a unified auto-saved watch list', async ({ page }) => {
  const savedProfiles: Array<{
    topics: Array<{ name: string }>
    watched_authors: string[]
    watched_organizations: string[]
  }> = []
  await page.route('**/api/profile', async (route) => {
    if (route.request().method() !== 'PUT') {
      await route.continue()
      return
    }
    const payload = route.request().postDataJSON() as typeof savedProfiles[number]
    savedProfiles.push(payload)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
  })
  await page.goto('/?view=profile')

  await expect(page.getByRole('heading', { name: '关注设置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存关注设置' })).toHaveCount(0)
  const saveStatus = page.locator('.auto-save-status')
  await expect(saveStatus).toContainText('已自动保存')
  const interests = page.locator('.interest-tags')
  const labels = await interests.locator('.interest-tag').allTextContents()
  expect(labels.length).toBeGreaterThan(0)
  for (const label of labels) expect(label).not.toMatch(/[\u3400-\u9fff]/)

  await interests.getByRole('button', { name: '添加关注项' }).click()
  const dialog = page.getByRole('dialog', { name: '添加关注项' })
  await expect(dialog).toBeVisible()

  const preset = dialog.locator('.interest-options button:not([disabled])').first()
  const presetLabel = (await preset.innerText()).trim()
  await preset.click()
  await expect(interests).toContainText(presetLabel)
  await expect.poll(() => savedProfiles.length).toBe(1)
  await expect(saveStatus).toContainText('已自动保存')

  const customLabel = 'Playwright UI verification topic'
  await dialog.getByPlaceholder('Search presets or type a new interest').fill(customLabel)
  await dialog.locator('.interest-custom-option').click()
  await expect(interests).toContainText(customLabel)
  await expect.poll(() => savedProfiles.length).toBe(2)
  expect(savedProfiles.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(true)

  await dialog.getByRole('button', { name: 'Author' }).click()
  await dialog.getByPlaceholder('Type an author name').fill('Jane Doe')
  await dialog.locator('.interest-custom-option').click()
  await expect(interests).toContainText('Jane Doe')
  await expect.poll(() => savedProfiles.at(-1)?.watched_authors).toContain('Jane Doe')

  await dialog.getByRole('button', { name: 'Organization' }).click()
  await dialog.getByPlaceholder('Type an organization name').fill('NVIDIA Research')
  await dialog.locator('.interest-custom-option').click()
  await expect(interests).toContainText('NVIDIA Research')
  await expect.poll(() => savedProfiles.at(-1)?.watched_organizations).toContain('NVIDIA Research')

  await dialog.getByRole('button', { name: '关闭' }).click()
  await interests.getByRole('button', { name: `删除 ${customLabel}` }).click()
  await expect.poll(() => savedProfiles.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(false)
  expect(savedProfiles.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(false)
})
