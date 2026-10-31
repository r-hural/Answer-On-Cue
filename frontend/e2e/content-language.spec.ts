import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

/**
 * The M6 acceptance in the browser: an English subject from its document to the feedback, Polish
 * unchanged, and a backup that keeps both languages. English is not enabled on the shared e2e
 * server, so the English subject is seeded and a second enabled language is only stubbed in the
 * language list; no language, setting or restore is ever changed here.
 * speech_mid.wav loops: 2.0 s silence, speech 2.0-5.51 s, 2.0 s silence (7.51 s per loop).
 */

const GUARD = { 'X-AnswerOnCue': '1' }
const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seedScript = path.resolve(import.meta.dirname, 'seed_content_language.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const QUESTIONS = [
  'Explain what ostracism was and why a citizen could be sent into exile.',
  'Describe the role of the Council of Five Hundred in Athenian democracy.',
]
const DOCUMENT =
  'Athenian democracy rested on the popular assembly.\n\n' +
  'Ostracism was a vote on pottery shards that sent a citizen into exile for ten years.\n\n' +
  'The Council of Five Hundred prepared the business of the assembly.\n'
const SUPPORTED = ['pl', 'en', 'de', 'fr', 'es', 'it', 'uk']
const VOICE_WRONG_LANGUAGE = 'This voice is for another language. Choose a voice for the content language.'

const unique = (prefix: string) => `${prefix} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

/** Runs the Python seed from backend/ and returns its one JSON line. */
function seed<T>(args: string[]): T {
  const out = execFileSync(python, [seedScript, ...args], {
    cwd: backend,
    env: { ...process.env, AOC_DATA_DIR: dataDir },
    encoding: 'utf-8',
  })
  return JSON.parse(out.trim().split('\n').pop()!) as T
}

const recording = (page: Page) => page.locator('.live', { hasText: 'Recording' })

/** Shows English as a second enabled language in the browser only. */
async function stubEnglishEnabled(page: Page): Promise<void> {
  await page.route('**/api/languages', async (route) => {
    const response = await route.fetch()
    const rows = ((await response.json()) as { code: string; enabled: boolean }[]).map((row) =>
      row.code === 'en' ? { ...row, enabled: true } : row,
    )
    await route.fulfill({ response, json: rows })
  })
}

/** Records the requests that would change shared server state; each test checks the list is empty. */
function watchSharedState(page: Page): string[] {
  const forbidden: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    const method = request.method()
    if (method === 'PUT' && (url.includes('/api/languages') || url.includes('/api/settings'))) forbidden.push(`${method} ${url}`)
    if (url.includes('refresh=true') || url.includes('/apply')) forbidden.push(`${method} ${url}`)
  })
  return forbidden
}

/** A practice round of 2 questions of the named subject, up to the first question. */
async function startRound(page: Page, subjectName: string): Promise<void> {
  await page.goto('/practice')
  await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
  await page.getByLabel('Subject', { exact: true }).selectOption({ label: subjectName })
  await page.getByRole('radio', { name: /^Practice/ }).check()
  await page.getByLabel('Main questions').fill('2')
  await page.getByLabel('Preparation (s)').fill('10')
  await page.getByLabel('Answer time (s)').fill('10')
  await page.getByLabel('Read questions aloud').selectOption('off')
  await page.getByRole('checkbox', { name: /^Follow-up questions/ }).uncheck()
  await expect(page.getByText('All settings work together.')).toBeVisible()
  await page.getByRole('button', { name: 'Check microphone and start' }).click()

  await expect(page).toHaveURL(/\/practice\/\d+\/microphone$/)
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByText('The microphone works.')).toBeVisible()
  await page.getByRole('button', { name: 'Start the round' }).click()
  await expect(page).toHaveURL(/\/rounds\/\d+$/)
}

test('an English subject from the document to the feedback', async ({ page }) => {
  test.setTimeout(180_000)
  const forbidden = watchSharedState(page)
  const name = unique('E2E English')

  const { subject_id: subjectId } = seed<{ subject_id: number }>(['subject', name])
  const uploaded = await page.request.post(`/api/subjects/${subjectId}/documents`, {
    headers: GUARD,
    multipart: { file: { name: 'athens.txt', mimeType: 'text/plain', buffer: Buffer.from(DOCUMENT) } },
  })
  expect(uploaded.ok()).toBe(true)
  const bank = seed<{ topic_id: number; question_ids: number[] }>(['bank', String(subjectId)])
  expect(bank.question_ids).toHaveLength(2)
  await stubEnglishEnabled(page)

  // the library: the name is a name (no lang), the chip names the language
  await page.goto('/library')
  const link = page.getByRole('link', { name, exact: true })
  await expect(link).toBeVisible()
  await expect(link).not.toHaveAttribute('lang')
  const row = page.locator('li', { has: link })
  await expect(row.locator('.chip', { hasText: 'English' })).toBeVisible()
  const polishRow = page.locator('li', { has: page.getByRole('link', { name: 'E2E exam', exact: true }) })
  await expect(polishRow.locator('.chip', { hasText: 'Polish' })).toBeVisible()

  // the subject page: the chip under the heading, the topic in en-GB
  await page.goto(`/library/${subjectId}`)
  await expect(page.locator('.page-head .chip', { hasText: 'English' })).toBeVisible()
  // the topic list is a closed panel: its title is in the page, in en-GB
  await expect(page.locator('[lang="en-GB"]', { hasText: 'Athenian democracy' }).first()).toBeAttached()

  // the bank: the question is en-GB, and "a citizen" keeps an ordinary space
  await page.goto(`/library/${subjectId}/questions`)
  const question = page.locator('h3.q-text[lang="en-GB"]', { hasText: 'ostracism' })
  await expect(question).toHaveText(QUESTIONS[0])
  const text = await question.evaluate((element) => element.textContent)
  expect(text).toContain(' a citizen')
  expect(text).not.toContain('\u00a0')

  // a practice round with the fake microphone through the real pipeline
  await startRound(page, name)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2')
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2: feedback', { timeout: 40_000 })
  await expect(page.locator('.transcript[lang="en-GB"]')).toContainText('Ostracism was a vote on pottery shards')
  const asked = (await page.locator('.content-q[lang="en-GB"]').first().textContent()) ?? ''
  expect(QUESTIONS).toContain(asked)
  await expect(page.getByText('Name the institution that held the vote.')).toBeVisible()
  await expect(page.locator('[lang="pl-PL"]')).toHaveCount(0)
  await page.keyboard.press('Enter')

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2')
  await expect(page.getByText('Listening', { exact: true }).or(recording(page))).toBeVisible()
  await page.keyboard.press('n')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2: feedback')
  await page.getByRole('button', { name: /See the round results/ }).click()

  // the summary lists both questions in en-GB, and the round remembers its language
  await expect(page).toHaveURL(/\/rounds\/\d+\/summary$/)
  const roundId = /\/rounds\/(\d+)\/summary$/.exec(page.url())![1]
  await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible()
  const lines = page.locator('.q-line[lang="en-GB"]')
  await expect(lines).toHaveCount(2)
  const shown = (await lines.allTextContents()).map((line) => line.replace(/^\s*\d+\s*/, '').trim())
  for (const wanted of QUESTIONS) expect(shown.some((line) => line.includes(wanted))).toBe(true)
  const round = await page.request.get(`/api/rounds/${roundId}`)
  expect(((await round.json()) as { config: { language: string } }).config.language).toBe('en')

  // the voice: an English voice or the browser voice, never a Polish one
  const spoken = await page.request.post('/api/voice/speak', { headers: GUARD, data: { text: QUESTIONS[0], language: 'en' } })
  expect(spoken.status()).toBe(200)
  const voice = (await spoken.json()) as { provider: string; voice: string }
  expect(voice.provider === 'browser' || /^en[_-]GB-/.test(voice.voice)).toBe(true)
  expect(voice.voice.startsWith('pl')).toBe(false)
  const wrong = await page.request.post('/api/voice/speak', {
    headers: GUARD,
    data: { text: QUESTIONS[0], language: 'en', provider: 'piper', voice: 'pl_PL-bass-high' },
  })
  expect(wrong.status()).toBe(422)
  expect(JSON.stringify(await wrong.json())).toContain(VOICE_WRONG_LANGUAGE)

  expect(forbidden).toEqual([])
})

test('Polish is unchanged', async ({ page }) => {
  const forbidden = watchSharedState(page)
  const listed = await page.request.get('/api/subjects')
  const subjects = (await listed.json()) as { id: number; name: string }[]
  const exam = subjects.find((subject) => subject.name === 'E2E exam')!
  expect(exam).toBeTruthy()

  await page.goto(`/library/${exam.id}/questions`)
  const question = page.locator('h3.q-text[lang="pl-PL"]', { hasText: 'ostracyzm' })
  await expect(question).toBeVisible()
  // Playwright's text matchers treat U+00A0 as a space, so the string is compared as is
  const text = await question.evaluate((element) => element.textContent)
  expect(text).toContain('w\u00a0starożytnych')

  await page.goto('/library')
  const link = page.getByRole('link', { name: 'E2E exam', exact: true })
  await expect(link).toBeVisible()
  await expect(link).not.toHaveAttribute('lang')
  expect(forbidden).toEqual([])
})

test('a backup keeps both languages', async ({ page }, testInfo) => {
  const forbidden = watchSharedState(page)
  const name = unique('E2E English backup')
  seed<{ subject_id: number }>(['subject', name])

  const made = await page.request.post('/api/data/backups', { headers: GUARD })
  expect(made.status()).toBe(201)
  const backupName = ((await made.json()) as { name: string }).name
  const download = await page.request.get(`/api/data/backups/${backupName}`)
  expect(download.ok()).toBe(true)
  const zip = testInfo.outputPath('backup.zip')
  writeFileSync(zip, await download.body())

  const inside = seed<{ subjects: Record<string, string>; languages: string[] }>(['inspect', zip])
  expect(inside.subjects[name]).toBe('en')
  expect(inside.subjects['E2E exam']).toBe('pl')
  expect(inside.languages).toEqual(SUPPORTED)

  // the restore is previewed and discarded: applying it would replace the database every spec uses
  const preview = await page.request.post('/api/data/restore', {
    headers: GUARD,
    multipart: { file: { name: 'backup.zip', mimeType: 'application/zip', buffer: readFileSync(zip) } },
  })
  expect(preview.status()).toBe(200)
  const staged = (await preview.json()) as { token: string; counts: { subjects: number } }
  expect(staged.counts.subjects).toBeGreaterThanOrEqual(2)
  const discarded = await page.request.delete(`/api/data/restore/${staged.token}`, { headers: GUARD })
  expect(discarded.status()).toBe(204)
  expect(forbidden).toEqual([])
})
