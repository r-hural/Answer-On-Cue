import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

// click_only.wav never starts a recording by itself: every answer here is started with
// "Answer now" and finished with "Finish answer" (or by its deadline). The server runs e2e_app.py.

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_recovery.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')
const GUARD = { 'X-AnswerOnCue': '1' }

interface RoundState {
  id: number
  status: string
  phase: string
  current_attempt: { id: number; parent_attempt_id: number | null } | null
}

let subjectId: number

async function createRound(page: Page, count: number, answerSeconds: number): Promise<number> {
  const response = await page.request.post('/api/rounds', {
    headers: GUARD,
    data: {
      subject_id: subjectId,
      config: {
        scope: { kind: 'subject' },
        feedback_timing: 'practice',
        question_count: count,
        prep_seconds: 30,
        answer_seconds: answerSeconds,
        question_audio: { mode: 'off' },
      },
    },
  })
  expect(response.status()).toBe(201)
  return ((await response.json()) as RoundState).id
}

async function roundOf(page: Page, roundId: number): Promise<RoundState> {
  return (await (await page.request.get(`/api/rounds/${roundId}`)).json()) as RoundState
}

async function throughMicrophone(page: Page, url: RegExp = /\/rounds\/\d+$/): Promise<void> {
  await page.getByRole('button', { name: 'Allow microphone', exact: true }).click()
  await expect(page.getByText('The microphone works.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Start the round', exact: true }).click()
  await expect(page).toHaveURL(url)
}

async function startRound(page: Page, roundId: number): Promise<void> {
  await page.goto(`/practice/${roundId}/microphone`)
  await throughMicrophone(page)
}

const answerNow = (page: Page) => page.getByRole('button', { name: /^Answer now\b/ })
const finishAnswer = (page: Page) => page.getByRole('button', { name: /^Finish answer\b/ })

/** "Answer now", then waits until "Finish answer" can be pressed. */
async function startAnswer(page: Page): Promise<void> {
  await expect(answerNow(page)).toBeEnabled()
  await answerNow(page).click()
  await expect(finishAnswer(page)).toBeEnabled()
}

test.describe('recovery', () => {
  test.beforeAll(() => {
    const out = execFileSync(python, [seed, `E2E recovery ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    subjectId = (JSON.parse(out.trim().split('\n').pop() as string) as { subject_id: number }).subject_id
  })

  test('a reload while the answer uploads keeps one answer', async ({ page }) => {
    test.setTimeout(90_000)
    const roundId = await createRound(page, 1, 30)
    await startRound(page, roundId)
    await page.route('**/api/attempts/*/audio', (route) => route.abort())
    await startAnswer(page)
    const audio = page.waitForRequest(/\/api\/attempts\/(\d+)\/audio$/)
    await finishAnswer(page).click()
    const attemptId = Number(/\/api\/attempts\/(\d+)\/audio$/.exec((await audio).url())?.[1])
    expect(attemptId).toBeGreaterThan(0)
    await page.unroute('**/api/attempts/*/audio')

    page.on('dialog', (dialog) => void dialog.accept())
    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: /^Question 1 of 1: feedback$/ })).toBeVisible({ timeout: 30_000 })
    const result = (await (await page.request.get(`/api/attempts/${attemptId}/result`)).json()) as {
      status: string
      recording: { id: number; duration_ms: number } | null
    }
    expect(result.status).toBe('ready')
    expect(result.recording).not.toBeNull()
    expect((await roundOf(page, roundId)).current_attempt?.id).toBe(attemptId)
  })

  test('a reload during preparation offers to continue and restarts the question', async ({ page }) => {
    test.setTimeout(90_000)
    const roundId = await createRound(page, 2, 30)
    await startRound(page, roundId)
    await expect(answerNow(page)).toBeEnabled()
    const interrupted = (await roundOf(page, roundId)).current_attempt?.id as number

    page.on('dialog', (dialog) => void dialog.accept())
    await page.reload()
    await expect(page.getByRole('heading', { level: 2, name: 'Continue your round?', exact: true })).toBeVisible()
    await expect(page.getByText('Question 1: interrupted. It starts again from the beginning.', { exact: true })).toBeVisible()

    await page.getByRole('link', { name: 'Check microphone and continue', exact: true }).click()
    await expect(page).toHaveURL(/\/practice\/\d+\/microphone\?resume=1$/)
    await throughMicrophone(page, /\/rounds\/\d+(\?resume=1)?$/)
    await expect(page).toHaveURL(/\/rounds\/\d+$/)
    await expect(page.getByRole('heading', { level: 1, name: /^Question 1 of 2$/ })).toBeVisible()
    await expect(answerNow(page)).toBeVisible()
    const state = await roundOf(page, roundId)
    expect(state.phase).toBe('preparation')
    expect(state.current_attempt?.parent_attempt_id).toBe(interrupted)
  })

  test('a blocked tab finishes the answer at its deadline', async ({ page }) => {
    test.setTimeout(90_000)
    const roundId = await createRound(page, 1, 5)
    await startRound(page, roundId)
    await startAnswer(page)
    const attemptId = (await roundOf(page, roundId)).current_attempt?.id as number

    // blocks every page timer for 8 s while the microphone keeps capturing
    await page.evaluate(() => {
      const end = performance.now() + 8000
      while (performance.now() < end) {
        // busy
      }
    })
    const processing = page.getByText('Your answer is saved', { exact: true })
    const feedback = page.getByRole('heading', { level: 1, name: /^Question 1 of 1: feedback$/ })
    await expect(processing.or(feedback).first()).toBeVisible({ timeout: 5_000 })
    await expect(feedback).toBeVisible({ timeout: 30_000 })
    const result = (await (await page.request.get(`/api/attempts/${attemptId}/result`)).json()) as {
      recording: { duration_ms: number } | null
    }
    expect(result.recording).not.toBeNull()
    expect(result.recording?.duration_ms).toBeLessThanOrEqual(5000 + 1000 + 500)
  })
})
