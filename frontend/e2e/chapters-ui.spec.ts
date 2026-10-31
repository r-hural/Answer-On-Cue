import { expect, test, type Page } from '@playwright/test'

/**
 * The Chapters UI against the e2e backend. The subject and one document are real; only the chapter
 * endpoints (not on this backend yet) and the reads that carry chapter fields are faked.
 */

const GUARD = { 'X-AnswerOnCue': '1' }
const FIRST = 1001
const SECOND = 1002

interface Seeded {
  id: number
  name: string
  documentId: number
  title: string
  versionId: number
  /** The uploaded document as the API returned it. */
  document: { latest_version: object } & Record<string, unknown>
}

async function seed(page: Page): Promise<Seeded> {
  const name = `E2E chapters ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const created = await page.request.post('/api/subjects', { headers: GUARD, data: { name, description: null, language: 'pl' } })
  expect(created.ok()).toBe(true)
  const id = ((await created.json()) as { id: number }).id
  const uploaded = await page.request.post(`/api/subjects/${id}/documents`, {
    headers: GUARD,
    multipart: {
      file: { name: 'grecja.txt', mimeType: 'text/plain', buffer: Buffer.from('Ateny i Sparta.\n\nReformy Solona.\n') },
    },
  })
  expect(uploaded.ok()).toBe(true)
  const doc = (await uploaded.json()) as Seeded['document'] & { id: number; title: string; latest_version: { id: number } }
  return { id, name, documentId: doc.id, title: doc.title, versionId: doc.latest_version.id, document: doc }
}

const chapters = (subjectId: number) => [
  { id: FIRST, subject_id: subjectId, name: null, is_default: true, ordinal: 0, document_count: 1, topic_count: 2, question_count: 0 },
  { id: SECOND, subject_id: subjectId, name: 'Rewolucja francuska', is_default: false, ordinal: 1, document_count: 1, topic_count: 1, question_count: 0 },
]

const topic = (id: number, title: string, chapterId: number) => ({
  id,
  title,
  description: null,
  origin: 'ai',
  status: 'active',
  objectives: [],
  passage_count: 2,
  question_count: 0,
  children: [],
  question_counts: {},
  chapter_id: chapterId,
})

/**
 * Fakes the chapter reads of the seeded subject as a subject with two chapters, answered from the
 * seeded data (no pass-through fetch, so nothing is in flight when a test ends); returns the moves.
 */
async function fakeChapters(page: Page, s: Seeded): Promise<unknown[]> {
  const moves: unknown[] = []
  const subject = { id: s.id, name: s.name, description: null, document_count: 2, chapter_count: 2 }
  await page.route(
    (url) => url.pathname === '/api/subjects',
    (route) => (route.request().method() === 'GET' ? route.fulfill({ json: [subject] }) : route.fallback()),
  )
  await page.route(
    (url) => url.pathname === `/api/subjects/${s.id}`,
    (route) => (route.request().method() === 'GET' ? route.fulfill({ json: subject }) : route.fallback()),
  )
  await page.route(`**/api/subjects/${s.id}/chapters`, (route) => route.fulfill({ json: chapters(s.id) }))
  const real = { ...s.document, chapter_id: FIRST }
  const other = {
    ...s.document,
    id: 990010,
    title: 'Rewolucja',
    chapter_id: SECOND,
    latest_version: { ...s.document.latest_version, id: 990011, filename: 'rewolucja.txt' },
  }
  await page.route(`**/api/subjects/${s.id}/documents`, (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [real, other] }) : route.fallback(),
  )
  await page.route(
    (url) => url.pathname === '/api/topics',
    (route) => {
      const chapter = Number(new URL(route.request().url()).searchParams.get('chapter_id'))
      const all = [topic(990101, 'Ustrój Aten', FIRST), topic(990102, 'Sparta', FIRST), topic(990103, 'Terror', SECOND)]
      return route.fulfill({ json: chapter ? all.filter((t) => t.chapter_id === chapter) : all })
    },
  )
  await page.route('**/api/documents/*/chapter', async (route) => {
    moves.push(route.request().postDataJSON())
    const documentId = Number(new URL(route.request().url()).pathname.split('/')[3])
    await route.fulfill({ json: { document_id: documentId, chapter_id: 1003, topics_moved: [990101, 990102] } })
  })
  return moves
}

test('chapter tabs switch the documents and topics', async ({ page }) => {
  const s = await seed(page)
  await fakeChapters(page, s)
  await page.goto(`/library/${s.id}`)
  const tabs = page.getByRole('navigation', { name: 'Chapters' })
  await expect(tabs.getByRole('link')).toHaveText(['Unnamed chapter', 'Rewolucja francuska'])
  await expect(tabs.getByRole('link', { name: 'Unnamed chapter', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: `Move ${s.title}`, exact: true })).toBeVisible()
  await expect(page.getByText('2 topics', { exact: true })).toBeVisible()

  await tabs.getByRole('link', { name: 'Rewolucja francuska', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/library/${s.id}\\?chapter=${SECOND}$`))
  await expect(page.getByRole('button', { name: 'Move Rewolucja', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: `Move ${s.title}`, exact: true })).toHaveCount(0)
  await expect(page.getByText('1 topic', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Chapter (optional)')).toHaveValue('Rewolucja francuska')
})

test('moving a document to a new chapter shows how many topics moved', async ({ page }) => {
  const s = await seed(page)
  const moves = await fakeChapters(page, s)
  await page.goto(`/library/${s.id}`)
  const move = page.getByRole('button', { name: `Move ${s.title}`, exact: true })
  await expect(move).toBeEnabled()
  await move.click()
  const dialog = page.getByRole('dialog', { name: 'Move to a chapter' })
  await expect(dialog.getByRole('radio', { name: 'Rewolucja francuska', exact: true })).toBeChecked()
  await dialog.getByRole('radio', { name: 'New chapter', exact: true }).check()
  await dialog.getByLabel('Name of the new chapter').fill('Starożytna Grecja')
  const submit = dialog.getByRole('button', { name: 'Move', exact: true })
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(page.getByText('Moved, with 2 topics.', { exact: true })).toBeVisible()
  await expect(dialog).toHaveCount(0)
  expect(moves).toEqual([{ new_chapter_name: 'Starożytna Grecja' }])
})

test('practice offers whole chapter by default for a chaptered subject', async ({ page }) => {
  const s = await seed(page)
  await fakeChapters(page, s)
  await page.goto(`/practice?subject=${s.id}&chapter=${SECOND}`)
  const chapter = page.getByLabel('Chapter', { exact: true })
  await expect(chapter).toHaveValue(String(SECOND))
  const scope = page.getByRole('radiogroup', { name: 'Scope' })
  await expect(scope.getByRole('radio', { name: 'Whole chapter', exact: true })).toBeChecked()
  await expect(scope.getByRole('radio', { name: 'Whole subject', exact: true })).not.toBeChecked()
})

test('practice opens on All questions and generation offers Only expand', async ({ page }) => {
  const s = await seed(page)
  await page.goto(`/practice?subject=${s.id}`)
  const questions = page.getByRole('radiogroup', { name: 'Questions' })
  await expect(questions.getByRole('radio', { name: /^All questions/ })).toBeChecked()
  await expect(questions.getByRole('radio', { name: /^My materials/ })).not.toBeChecked()

  await page.goto(`/library/${s.id}`)
  const sources = page.getByLabel('Sources', { exact: true })
  await expect(sources.getByRole('option')).toHaveText(['Only my materials', 'My materials + expand', 'Only expand'])
})

test('a finished extraction offers questions for that book', async ({ page }) => {
  const s = await seed(page)
  const state = { running: true }
  const job = (status: string, done: number) => ({
    id: 990201,
    kind: 'topics.extract',
    status,
    steps_total: 3,
    steps_done: done,
    error_message: null,
    summary: {},
    subject_id: s.id,
    document_version_id: s.versionId,
    resumable: false,
    usage: [],
    created_at: '2026-10-24T10:00:00',
    updated_at: '2026-10-24T10:00:00',
    subject_name: s.name,
    document_title: 'Grecja',
    chapter_id: null,
  })
  await page.route(
    (url) => url.pathname === '/api/jobs' && url.searchParams.get('live') === 'true',
    (route) => route.fulfill({ json: state.running ? [job('running', 1)] : [] }),
  )
  await page.route('**/api/jobs/990201', (route) => route.fulfill({ json: job('done', 3) }))
  await page.goto('/progress/history')
  await expect(page.getByRole('complementary', { name: 'Running tasks' })).toBeVisible()
  state.running = false
  const dialog = page.getByRole('dialog', { name: 'Topics ready for „Grecja”' })
  await expect(dialog).toBeVisible()
  const generate = dialog.getByRole('button', { name: 'Generate questions', exact: true })
  await expect(generate).toBeEnabled()
  await generate.click()
  await expect(page).toHaveURL(new RegExp(`/library/${s.id}\\?generate=${s.versionId}$`))
  await expect(page.getByRole('heading', { level: 1, name: s.name })).toBeVisible()
})
