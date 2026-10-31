import { expect, test, type Page } from '@playwright/test'

/** A fake extraction: the live task list is stubbed, the subject is real. */
async function fakeExtraction(page: Page, subjectId: number, subjectName: string) {
  const state = { running: true }
  const job = (status: string, done: number) => ({
    id: 990001,
    kind: 'topics.extract',
    status,
    steps_total: 9,
    steps_done: done,
    error_message: null,
    summary: {},
    subject_id: subjectId,
    document_version_id: 990002,
    resumable: false,
    usage: [],
    created_at: '2026-10-24T10:00:00',
    updated_at: '2026-10-24T10:00:00',
    subject_name: subjectName,
    document_title: 'Błony biologiczne',
  })
  await page.route(
    (url) => url.pathname === '/api/jobs' && url.searchParams.get('live') === 'true',
    (route) => route.fulfill({ json: state.running ? [job('running', 4)] : [] }),
  )
  await page.route('**/api/jobs/990001', (route) => route.fulfill({ json: job('done', 9) }))
  return state
}

async function newSubject(page: Page): Promise<{ id: number; name: string }> {
  const name = `E2E card ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`
  const response = await page.request.post('/api/subjects', { data: { name, description: null, language: 'pl' } })
  expect(response.ok()).toBe(true)
  return { id: ((await response.json()) as { id: number }).id, name }
}

test('floating card shows a running extraction on every page and opens its subject', async ({ page }) => {
  const subject = await newSubject(page)
  const state = await fakeExtraction(page, subject.id, subject.name)
  await page.goto('/progress/history')
  const card = page.getByRole('complementary', { name: 'Running tasks' })
  await expect(card).toBeVisible()
  await expect(card.getByText('Extracting topics')).toBeVisible()
  await expect(card.getByText('Błony biologiczne')).toBeVisible()
  await expect(card.getByText('4 of 9')).toBeVisible()
  await expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '4')

  await card.getByRole('link').click()
  await expect(page).toHaveURL(new RegExp(`/library/${subject.id}$`))
  await expect(page.getByRole('heading', { level: 1, name: subject.name })).toBeVisible()
  await expect(card).toBeVisible()

  state.running = false
  await expect(card.getByText('done')).toBeVisible()
  await expect(card.getByRole('progressbar')).toHaveCount(0)
})

test('floating card spans the phone width and stays off the recording screen', async ({ page }) => {
  const subject = await newSubject(page)
  await fakeExtraction(page, subject.id, subject.name)
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/library')
  const card = page.getByRole('complementary', { name: 'Running tasks' })
  await expect(card).toBeVisible()
  const box = (await card.boundingBox())!
  expect(Math.round(box.x)).toBe(16)
  expect(Math.round(box.width)).toBe(390 - 32)
  expect(Math.round(box.y + box.height)).toBe(800 - 16)

  await page.goto('/microphone')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(card).toHaveCount(0)
})
