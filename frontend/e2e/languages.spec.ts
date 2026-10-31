import { expect, test } from '@playwright/test'

/**
 * The content language of a subject against the e2e backend. Enabling a language is a global
 * setting, so it is never changed here: a second enabled language is only stubbed in the list.
 */

const GUARD = { 'X-AnswerOnCue': '1' }
const SUPPORTED = ['pl', 'en', 'de', 'fr', 'es', 'it', 'uk']
const LOCKED = "The language of a subject can't be changed after it is created."
const NOT_ENABLED = 'This content language is not enabled. Choose one of the enabled languages.'

const unique = () => `E2E languages ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

interface Language {
  code: string
  enabled: boolean
}

test('a subject gets a language when it is created, and keeps it', async ({ page }) => {
  const puts: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/api/languages')) puts.push(request.url())
  })

  const listed = await page.request.get('/api/languages')
  expect(listed.ok()).toBe(true)
  const languages = (await listed.json()) as Language[]
  expect(languages.map((row) => row.code)).toEqual(SUPPORTED)
  expect(languages.find((row) => row.code === 'pl')?.enabled).toBe(true)

  await page.goto('/library')
  const name = unique()
  await page.getByLabel('Subject name').fill(name)
  const select = page.getByLabel('Content language')
  const add = page.getByRole('button', { name: 'Add subject' })
  await expect(select).toHaveValue('')
  await expect(add).toBeDisabled()
  await expect(select.getByRole('option', { name: 'Polish (polski)', exact: true })).toBeAttached()
  await select.selectOption({ label: 'Polish (polski)' })
  await expect(add).toBeEnabled()
  await add.click()
  const link = page.getByRole('link', { name, exact: true })
  await expect(link).toBeVisible()
  const id = Number((await link.getAttribute('href'))?.split('/').pop())
  const subject = await page.request.get(`/api/subjects/${id}`)
  expect(((await subject.json()) as { language: string }).language).toBe('pl')

  const patched = await page.request.patch(`/api/subjects/${id}`, { headers: GUARD, data: { language: 'de' } })
  expect(patched.status()).toBe(422)
  expect(((await patched.json()) as { detail: string }).detail).toBe(LOCKED)

  await page.route('**/api/languages', async (route) => {
    const response = await route.fetch()
    const rows = ((await response.json()) as Language[]).map((row) =>
      row.code === 'de' ? { ...row, enabled: true } : row,
    )
    await route.fulfill({ response, json: rows })
  })
  await page.reload()
  const german = unique()
  await page.getByLabel('Subject name').fill(german)
  await expect(select.getByRole('option', { name: 'German (Deutsch)', exact: true })).toBeAttached()
  await select.selectOption({ label: 'German (Deutsch)' })
  await expect(add).toBeEnabled()
  await add.click()
  await expect(page.getByRole('alert')).toContainText(NOT_ENABLED)
  const subjects = (await (await page.request.get('/api/subjects')).json()) as { name: string }[]
  expect(subjects.some((row) => row.name === german)).toBe(false)
  expect(puts).toEqual([])
})
