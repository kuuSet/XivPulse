import { expect, test } from '@playwright/test'

test('research interests use compact English tags', async ({ page }) => {
  await page.goto('/?view=profile')

  await expect(page.getByRole('heading', { name: '关注设置' })).toBeVisible()
  const interests = page.locator('.interest-tags')
  const labels = await interests.locator('.interest-tag').allTextContents()
  expect(labels.length).toBeGreaterThan(0)
  for (const label of labels) expect(label).not.toMatch(/[\u3400-\u9fff]/)

  await interests.getByRole('button', { name: '添加关注标签' }).click()
  const dialog = page.getByRole('dialog', { name: '添加关注标签' })
  await expect(dialog).toBeVisible()

  const preset = dialog.locator('.interest-options button:not([disabled])').first()
  const presetLabel = (await preset.innerText()).trim()
  await preset.click()
  await expect(interests).toContainText(presetLabel)

  const customLabel = 'Playwright UI verification topic'
  await dialog.getByPlaceholder('Search presets or type a new interest').fill(customLabel)
  await dialog.locator('.interest-custom-option').click()
  await expect(interests).toContainText(customLabel)
})
