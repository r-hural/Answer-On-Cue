import { expect, test, type Page } from '@playwright/test'

// speech_mid.wav loops: 2.0 s silence, speech 2.0–5.51 s, 2.0 s silence (7.51 s per loop).
// Every /api/attempts/... call is answered here; the round itself runs on the real backend.

interface Recorded {
  events: { id: number; body: Record<string, unknown>; at: number }[]
  audio: { id: number; body: Buffer; at: number }[]
}

async function stubAttempts(page: Page, stage = 'transcribing'): Promise<Recorded> {
  const recorded: Recorded = { events: [], audio: [] }
  await page.route('**/api/attempts/**', async (route) => {
    const request = route.request()
    const match = /\/api\/attempts\/(\d+)\/(events|audio|result)$/.exec(new URL(request.url()).pathname)
    if (!match) return route.fulfill({ status: 404, json: { detail: 'Not Found' } })
    const id = Number(match[1])
    if (match[2] === 'events') {
      recorded.events.push({ id, body: request.postDataJSON() as Record<string, unknown>, at: Date.now() })
      return route.fulfill({ json: { attempt_id: id, stored: true } })
    }
    if (match[2] === 'audio') {
      recorded.audio.push({ id, body: request.postDataBuffer() ?? Buffer.alloc(0), at: Date.now() })
      return route.fulfill({ status: 201, json: { attempt_id: id, recording_id: id, duration_ms: 3000 } })
    }
    return route.fulfill({
      json: {
        attempt_id: id,
        outcome: 'answered',
        status: 'processing',
        stage,
        recording: { id, duration_ms: 3000 },
        failure: null,
        withheld: false,
        transcript: null,
        metrics: null,
        assessment: null,
      },
    })
  })
  return recorded
}

function sizeOf(text: string): string {
  return text.length <= 120 ? 'short' : text.length <= 240 ? 'medium' : 'long'
}

/** Sets up a 3-question round of the seeded subject and opens the exam screen. */
async function startRound(page: Page, feedback: 'Practice' | 'Exam'): Promise<void> {
  await page.goto('/practice')
  await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
  await page.getByLabel('Subject', { exact: true }).selectOption({ label: 'E2E exam' })
  await page.getByRole('radio', { name: new RegExp(`^${feedback}`) }).check()
  await page.getByLabel('Main questions').fill('3')
  await page.getByLabel('Preparation (s)').fill('10')
  await page.getByLabel('Answer time (s)').fill('10')
  await page.getByLabel('Read questions aloud').selectOption('off')
  await page.getByRole('checkbox', { name: /^Follow-up questions/ }).uncheck()
  await expect(page.getByText('All settings work together.')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /^Follow-up questions/ })).not.toBeChecked()
  await page.getByRole('button', { name: 'Check microphone and start' }).click()

  await expect(page).toHaveURL(/\/practice\/\d+\/microphone$/)
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByText('The microphone works.')).toBeVisible()
  await page.getByRole('button', { name: 'Start the round' }).click()
  await expect(page).toHaveURL(/\/rounds\/\d+$/)
}

async function questionText(page: Page): Promise<string> {
  const question = page.locator('.content-q').first()
  await expect(question).toBeVisible()
  const text = (await question.textContent()) ?? ''
  await expect(question).toHaveAttribute('data-size', sizeOf(text))
  return text
}

const recording = (page: Page) => page.locator('.live', { hasText: 'Recording' })

test('exam round of 3 from setup to the end', async ({ page }) => {
  test.setTimeout(120_000)
  const recorded = await stubAttempts(page)
  await startRound(page, 'Exam')

  // Question 1: speech starts the recording by itself; Space finishes it.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 3')
  await questionText(page)
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  // Space does what the button does: it waits until the server has taken the auto-start.
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')

  // Question 2: N is "I don't know".
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 3')
  await questionText(page)
  await expect(page.getByText('Listening', { exact: true })).toBeVisible()
  await page.keyboard.press('n')

  // Question 3: "Answer now", then "Finish answer".
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 3 of 3')
  await questionText(page)
  await page.getByRole('button', { name: /Answer now/ }).click({ timeout: 3000 }).catch(() => undefined)
  await expect(recording(page)).toBeVisible()
  await page.waitForTimeout(1500)
  await page.getByRole('button', { name: /Finish answer/ }).click()

  // The stubbed attempts never reach the server, so it shows both answers as still being processed.
  await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible()
  await expect(page.getByText(/Provisional results\./)).toBeVisible()
  await expect(page.getByText(/2 are still being processed/)).toBeVisible()

  await expect.poll(() => recorded.audio.length, { timeout: 15_000 }).toBe(2)
  expect(recorded.events).toHaveLength(2)
  const ids = recorded.events.map((e) => e.id)
  expect(new Set(ids).size).toBe(2)
  expect(recorded.audio.map((a) => a.id).sort()).toEqual([...ids].sort())
  for (const id of ids) {
    const events = recorded.events.find((e) => e.id === id)!
    const audio = recorded.audio.find((a) => a.id === id)!
    expect(events.at).toBeLessThanOrEqual(audio.at)
    const text = audio.body.toString('latin1')
    const riff = text.indexOf('RIFF')
    expect(riff).toBeGreaterThanOrEqual(0)
    expect(text.slice(riff + 8, riff + 12)).toBe('WAVE')
  }
  const [first, second] = recorded.events
  expect(first.body.start_reason).toBe('auto')
  expect(Number(first.body.pre_roll_ms)).toBeGreaterThan(0)
  expect(first.body.interrupted).toBe(false)
  expect(first.body.listening_available).toBe(true)
  // The looping fixture can win the race to the click, so "auto" is accepted for question 3.
  expect(['button', 'auto']).toContain(second.body.start_reason)
})

test('practice round reaches processing', async ({ page }) => {
  test.setTimeout(90_000)
  await stubAttempts(page, 'transcribing')
  await startRound(page, 'Practice')

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 3')
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await page.waitForTimeout(1000)
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')

  await expect(page.getByText('Your answer is saved')).toBeVisible()
  await expect(page.getByText('Writing down what you said').locator('..')).toContainText('In progress')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 3')
})
