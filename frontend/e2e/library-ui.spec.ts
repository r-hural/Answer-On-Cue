import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_library.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

test.describe('topic editor', () => {
  let subjectId: number

  test.beforeAll(() => {
    const out = execFileSync(python, [seed, `E2E topics ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    subjectId = (JSON.parse(out.trim().split('\n').pop()!) as { subject_id: number }).subject_id
  })

  const names = (page: Page) => page.getByRole('textbox', { name: 'Topic name' })
  const rowOf = (page: Page, objective: string) =>
    page.getByRole('listitem').filter({ hasText: objective })

  test('topic editor: rename, merge, split, remove', async ({ page }) => {
    await page.goto(`/library/${subjectId}/topics`)
    await expect(page.getByRole('heading', { level: 1, name: 'Topics' })).toBeVisible()
    await expect(names(page)).toHaveCount(3)

    // Rename the first topic; the name is saved 800 ms after the last key.
    const saved = page.waitForResponse(
      (response) => response.request().method() === 'PATCH' && /\/api\/topics\/\d+$/.test(response.url()),
    )
    await names(page).first().fill('Reformy Solona i Klejstenesa')
    await saved
    await page.reload()
    await expect(names(page).first()).toHaveValue('Reformy Solona i Klejstenesa')

    // Merge the first two topics.
    await page.getByRole('checkbox', { name: /^Select topic: Reformy Solona i Klejstenesa/ }).check()
    await page.getByRole('checkbox', { name: 'Select topic: Reformy Klejstenesa' }).check()
    await page.getByRole('button', { name: /^Merge selected/ }).click()
    const merge = page.getByRole('dialog')
    await expect(merge.getByLabel('Name of the merged topic')).toHaveValue('Reformy Solona i Klejstenesa')
    await merge.getByLabel('Name of the merged topic').fill('Reformy Aten')
    await merge.getByRole('button', { name: 'Merge' }).click()
    await expect(page.getByText('Merged into „Reformy Aten”.')).toBeVisible()
    await expect(names(page)).toHaveCount(2)

    // Split the merged topic (four objectives) into two.
    await rowOf(page, 'Cel S1').getByRole('button', { name: 'Split' }).click()
    const split = page.getByRole('dialog')
    await split.getByLabel('New topic 1').fill('Solon')
    await split.getByLabel('New topic 2').fill('Klejstenes')
    await split.getByRole('button', { name: 'Split' }).click()
    await expect(page.getByText('Split into 2 topics; their questions are marked for review.')).toBeVisible()
    await expect(names(page)).toHaveCount(3)

    // Remove the last original topic.
    await rowOf(page, 'Cel O1').getByRole('button', { name: 'Remove' }).click()
    const remove = page.getByRole('dialog')
    await expect(remove.getByText('0 questions will be retired.')).toBeVisible()
    await remove.getByRole('button', { name: 'Remove' }).click()
    await expect(page.getByText(/^Removed „Ostracyzm”; 0 questions retired\.$/)).toBeVisible()

    await expect(names(page)).toHaveCount(2)
    await expect(names(page).nth(0)).toHaveValue('Solon')
    await expect(names(page).nth(1)).toHaveValue('Klejstenes')
  })
})

interface Recorded {
  generate: unknown[]
  rounds: unknown[]
}

/** Answers every AI-backed endpoint of the quick practice flow with contract-shaped data. */
async function stubAi(page: Page): Promise<Recorded> {
  const recorded: Recorded = { generate: [], rounds: [] }
  let extracted = false
  let generated = false
  const topic = (id: number, title: string) => ({
    id,
    title,
    description: null,
    origin: 'ai',
    status: 'active',
    objectives: [{ id: id * 10, text: `Cel ${title}` }],
    passage_count: 2,
    question_count: generated && id !== 903 ? 2 : 0,
    children: [],
  })
  const topics = () => [topic(901, 'Budowa błony'), topic(902, 'Transport przez błonę'), topic(903, 'Mitochondria')]
  const job = (id: number, kind: string, subjectId: number) => ({
    id,
    kind,
    status: 'done',
    steps_total: 3,
    steps_done: 3,
    error_message: null,
    subject_id: subjectId,
    document_version_id: null,
    resumable: false,
    usage: [],
  })

  await page.route('**/api/topics**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === 'POST' && url.pathname === '/api/topics/extract') {
      extracted = true
      return route.fulfill({ status: 202, json: { job_id: 7 } })
    }
    if (url.pathname === '/api/topics/terms') {
      return route.fulfill({
        json: extracted ? [{ term: 'fosfolipidy', definition: null, topic_id: 901 }] : [],
      })
    }
    return route.fulfill({ json: extracted ? topics() : [] })
  })
  await page.route('**/api/jobs**', async (route) => {
    const url = new URL(route.request().url())
    const subjectId = Number(url.searchParams.get('subject_id') ?? 0)
    const single = /\/api\/jobs\/(\d+)$/.exec(url.pathname)
    if (single) {
      const id = Number(single[1])
      return route.fulfill({ json: job(id, id === 7 ? 'topics.extract' : 'questions.generate', subjectId) })
    }
    const list = []
    if (generated) list.push(job(8, 'questions.generate', subjectId))
    if (extracted) list.push(job(7, 'topics.extract', subjectId))
    return route.fulfill({ json: list })
  })
  await page.route('**/api/questions**', async (route) => {
    const request = route.request()
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/questions/generate') {
      recorded.generate.push(request.postDataJSON())
      generated = true
      return route.fulfill({ status: 202, json: { job_id: 8 } })
    }
    return route.fulfill({ json: generated ? Array.from({ length: 4 }, () => ({})) : [] })
  })
  await page.route('**/api/rounds**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'POST' && pathname === '/api/rounds') {
      recorded.rounds.push(request.postDataJSON())
      return route.fulfill({ status: 201, json: { id: 4242 } })
    }
    if (pathname === '/api/rounds/4242') {
      return route.fulfill({
        json: {
          id: 4242,
          subject_id: 1,
          status: 'active',
          phase: 'question',
          current_index: 0,
          total: 4,
          config: {},
          current: null,
          current_attempt: null,
          items: [],
          pause_count: 0,
          paused_ms: 0,
        },
      })
    }
    return route.continue()
  })
  return recorded
}

test('quick practice from Home to the microphone check', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  const recorded = await stubAi(page)
  const file = testInfo.outputPath(`Ateny ${Date.now()}-${Math.random().toString(36).slice(2, 7)}.txt`)
  writeFileSync(file, 'Ustrój Aten opierał się na zgromadzeniu ludowym. Ostracyzm służył ochronie demokracji.\n')

  await page.goto('/')
  await page.getByRole('link', { name: /^Quick practice/ }).first().click()
  await expect(page).toHaveURL(/\/quick$/)

  await page.getByLabel('Choose a file').setInputFiles(file)
  await page.getByRole('button', { name: /^Upload „/ }).click()

  // The extraction is answered by the stub, so the topics step follows by itself.
  await expect(page.getByRole('heading', { name: /^Topics found in/ })).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(3)
  await page.getByRole('checkbox', { name: 'Mitochondria' }).uncheck()
  await page.getByLabel('Questions', { exact: true }).fill('4')
  await page.getByRole('button', { name: 'Prepare questions' }).click()

  await expect(page.getByText('4 questions are ready')).toBeVisible()
  await page.getByRole('button', { name: 'Start' }).click()
  await expect(page).toHaveURL(/\/practice\/4242\/microphone$/)

  const body = recorded.generate[0] as { topic_ids: number[]; per_topic: number; style: string }
  expect(body.topic_ids).toEqual([901, 902])
  expect(body.per_topic).toBe(2)
  const round = recorded.rounds[0] as {
    config: { scope: { kind: string; topic_ids: number[] }; feedback_timing: string; question_count: number }
  }
  expect(round.config.scope).toMatchObject({ kind: 'topics', topic_ids: [901, 902] })
  expect(round.config.feedback_timing).toBe('practice')
  expect(round.config.question_count).toBe(4)
})

test('round defaults survive a reload', async ({ page }) => {
  // The shared e2e server's settings never change: GET is served from a copy of the real first
  // answer and PUT is merged into that copy (persisting is test_settings_round_defaults.py's).
  let stored: Record<string, unknown> | null = null
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() === 'PUT') {
      stored = { ...stored, ...(route.request().postDataJSON() as Record<string, unknown>) }
      return route.fulfill({ json: stored })
    }
    if (stored === null) stored = (await (await route.fetch()).json()) as Record<string, unknown>
    return route.fulfill({ json: stored })
  })
  await page.goto('/settings')
  const timing = page.locator('#targets')
  await timing.getByLabel('Preparation (s)').fill('15')
  await timing.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(timing.getByText('Defaults saved.')).toBeVisible()

  await page.reload()
  await expect(page.locator('#targets').getByLabel('Preparation (s)')).toHaveValue('15')
})
