import { expect, test } from '@playwright/test'

test('watch categories are peer sections and auto-save independently', async ({ page }) => {
  const savedInterests: Array<{
    topics: Array<{ name: string, description: string }>
    watched_authors: string[]
    watched_organizations: string[]
  }> = []
  await page.route('**/api/interests', async (route) => {
    if (route.request().method() !== 'PUT') {
      await route.continue()
      return
    }
    const payload = route.request().postDataJSON() as typeof savedInterests[number]
    savedInterests.push(payload)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
  })
  await page.goto('/?view=interests')

  await expect(page.getByRole('heading', { name: '关注设置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存关注设置' })).toHaveCount(0)
  const saveStatus = page.locator('.auto-save-status')
  await expect(saveStatus).toContainText('已自动保存')
  const topicSection = page.locator('.form-section').filter({ has: page.getByRole('heading', { name: '关注标签', exact: true }) })
  const authorSection = page.locator('.form-section').filter({ has: page.getByRole('heading', { name: '关注作者', exact: true }) })
  const organizationSection = page.locator('.form-section').filter({ has: page.getByRole('heading', { name: '关注机构', exact: true }) })
  await expect(topicSection).toBeVisible()
  await expect(authorSection).toBeVisible()
  await expect(organizationSection).toBeVisible()

  const labels = await topicSection.locator('.interest-tag').allTextContents()
  expect(labels.length).toBeGreaterThan(0)
  for (const label of labels) expect(label).not.toMatch(/[\u3400-\u9fff]/)

  await topicSection.getByRole('button', { name: '添加关注标签' }).click()
  let dialog = page.getByRole('dialog', { name: '添加关注标签' })
  await expect(dialog).toBeVisible()

  const preset = dialog.locator('.interest-options button:not([disabled])').first()
  const presetLabel = (await preset.innerText()).trim()
  await preset.click()
  await expect(topicSection).toContainText(presetLabel)
  await expect.poll(() => savedInterests.length).toBe(1)
  await expect(saveStatus).toContainText('已自动保存')

  const customLabel = 'Playwright UI verification topic'
  const customDescription = 'Systems research for testing LLM interest descriptions in the user interface.'
  await dialog.getByPlaceholder('Search presets or type a new interest').fill(customLabel)
  await dialog.getByPlaceholder('Describe the research problems, methods, systems, and application boundary covered by this tag.').fill(customDescription)
  await dialog.locator('.interest-custom-option').click()
  await expect(topicSection).toContainText(customLabel)
  await expect.poll(() => savedInterests.length).toBe(2)
  expect(savedInterests.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(true)
  expect(savedInterests.at(-1)?.topics.find((topic) => topic.name === customLabel)?.description).toBe(customDescription)

  await dialog.getByRole('button', { name: '关闭' }).click()
  await topicSection.getByRole('button', { name: `编辑 ${customLabel}` }).click()
  dialog = page.getByRole('dialog', { name: `编辑 ${customLabel}` })
  const editedDescription = 'Systems research for LLM screening, ranking, and compact user-interface verification.'
  await dialog.getByPlaceholder('Describe the research problems, methods, systems, and application boundary covered by this tag.').fill(editedDescription)
  await dialog.getByRole('button', { name: '保存描述' }).click()
  await expect.poll(() => savedInterests.at(-1)?.topics.find((topic) => topic.name === customLabel)?.description).toBe(editedDescription)

  await authorSection.getByRole('button', { name: '添加关注作者' }).click()
  dialog = page.getByRole('dialog', { name: '添加关注作者' })
  await dialog.getByPlaceholder('Type an author name').fill('Jane Doe')
  await dialog.locator('.interest-custom-option').click()
  await expect(authorSection).toContainText('Jane Doe')
  await expect.poll(() => savedInterests.at(-1)?.watched_authors).toContain('Jane Doe')

  await dialog.getByRole('button', { name: '关闭' }).click()
  await organizationSection.getByRole('button', { name: '添加关注机构' }).click()
  dialog = page.getByRole('dialog', { name: '添加关注机构' })
  await dialog.getByPlaceholder('Type an organization name').fill('NVIDIA Research')
  await dialog.locator('.interest-custom-option').click()
  await expect(organizationSection).toContainText('NVIDIA Research')
  await expect.poll(() => savedInterests.at(-1)?.watched_organizations).toContain('NVIDIA Research')

  await dialog.getByRole('button', { name: '关闭' }).click()
  await topicSection.getByRole('button', { name: `删除 ${customLabel}` }).click()
  await expect.poll(() => savedInterests.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(false)
  expect(savedInterests.at(-1)?.topics.some((topic) => topic.name === customLabel)).toBe(false)
  for (const saved of savedInterests) {
    for (const topic of saved.topics) {
      for (const field of ['query', 'context_keywords', 'negative_keywords', 'weight']) {
        expect(topic).not.toHaveProperty(field)
      }
    }
  }
})
