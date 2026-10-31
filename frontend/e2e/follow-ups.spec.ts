import { expect, test, type Page } from '@playwright/test'

// speech_mid.wav loops: 2.0 s silence, speech 2.0-5.51 s, 2.0 s silence (7.51 s per loop).
// The server runs e2e_app.py: the real pipeline, with a fake transcriber, assessor and follow-up
// generator (which fails for questions about the eklezja). Nothing is stubbed in the browser.

const FOLLOW_UP_TEXT = 'Wspomniałeś o głosowaniu na skorupach. Wyjaśnij, kogo w ten sposób wygnano i dlaczego.'
const NO_ANSWER = 'No follow-up question: follow-ups are built from what you said, and there was no answer.'

const recording = (page: Page) => page.locator('.live', { hasText: 'Recording' })
const followUpCard = (page: Page) => page.getByRole('region', { name: 'Follow-up question', exact: true })
const followUpBox = (page: Page) =>
  page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Follow-up', exact: true }) })

async function startRound(page: Page, subject: string, feedback: 'Practice' | 'Exam', count: number): Promise<void> {
  await page.goto('/practice')
  await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
  await page.getByLabel('Subject', { exact: true }).selectOption({ label: subject })
  await page.getByRole('radio', { name: new RegExp(`^${feedback}`) }).check()
  await page.getByLabel('Main questions').fill(String(count))
  await page.getByLabel('Preparation (s)').fill('10')
  await page.getByLabel('Answer time (s)').fill('10')
  await page.getByLabel('Read questions aloud').selectOption('off')
  const followUps = page.getByRole('checkbox', { name: /^Follow-up questions/ })
  await followUps.check()
  await page.getByLabel('Follow-up preparation in seconds', { exact: true }).fill('5')
  await page.getByLabel('Follow-up answer time in seconds', { exact: true }).fill('10')
  await expect(page.getByText('All settings work together.')).toBeVisible()
  await expect(followUps).toBeChecked()
  await page.getByRole('button', { name: 'Check microphone and start' }).click()

  await expect(page).toHaveURL(/\/practice\/\d+\/microphone$/)
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByText('The microphone works.')).toBeVisible()
  await page.getByRole('button', { name: 'Start the round' }).click()
  await expect(page).toHaveURL(/\/rounds\/\d+$/)
}

test('practice: the follow-up comes before the feedback and is scored apart', async ({ page }) => {
  test.setTimeout(150_000)
  await startRound(page, 'E2E exam', 'Practice', 2)

  // Question 1: speech starts the recording; Space finishes it; the follow-up opens by itself.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2')
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')
  await expect(followUpCard(page).locator('.content-q[lang="pl-PL"]')).toHaveText(FOLLOW_UP_TEXT, { timeout: 40_000 })
  await expect(page.getByText('Follow-up to question 1', { exact: true })).toBeVisible()
  await expect(page.locator('.scores')).toHaveCount(0)

  // The follow-up answer, started by speech; then the feedback on the main answer with the box.
  await expect(recording(page)).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.scores')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2: feedback', { timeout: 40_000 })
  await expect(page.locator('.scores .band')).toBeVisible()
  await expect(followUpBox(page)).toContainText(FOLLOW_UP_TEXT)
  await expect(followUpBox(page).locator('.band')).toHaveText(/^(Weak|Needs improvement|Good|Very good)$/)
  await page.keyboard.press('Enter')

  // Question 2: N is "I don't know": no follow-up, the box says why.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2')
  await expect(page.getByText('Listening', { exact: true }).or(recording(page))).toBeVisible()
  await page.keyboard.press('n')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2: feedback')
  await expect(followUpBox(page)).toContainText(NO_ANSWER)
  await page.getByRole('button', { name: /See the round results/ }).click()

  await expect(page).toHaveURL(/\/rounds\/\d+\/summary$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible()
  const followUps = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Follow-ups', exact: true }) })
  await expect(followUps).toContainText(/^Follow-ups\s*1 asked/)
  await expect(followUps).toContainText('Scored separately; not in the round score.')
})

test('a failed follow-up never blocks the round', async ({ page }) => {
  test.setTimeout(120_000)
  await startRound(page, 'E2E follow-up failure', 'Practice', 1)
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')

  await expect(page.getByText('Couldn’t prepare a follow-up question.', { exact: true })).toBeVisible({ timeout: 40_000 })
  await expect(page.locator('.scores')).toHaveCount(0)
  // the retry is really sent (and accepted), not a no-op that leaves the same notice on screen
  await Promise.all([
    page.waitForResponse((r) => /\/api\/follow-ups\/\d+\/retry$/.test(r.url()) && r.status() === 202),
    page.getByRole('button', { name: 'Try again', exact: true }).click(),
  ])
  await expect(page.getByText('Couldn’t prepare a follow-up question.', { exact: true })).toBeVisible({ timeout: 40_000 })
  await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Continue without follow-up', exact: true }).click()

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 1: feedback')
  await expect(page.locator('.scores .band')).toBeVisible()
  await expect(followUpBox(page)).toContainText('No follow-up question: it could not be prepared.')
  await expect(followUpBox(page).locator('.band')).toHaveCount(0)
})

test('exam: the follow-up is asked mid-round without feedback', async ({ page }) => {
  test.setTimeout(120_000)
  await startRound(page, 'E2E exam', 'Exam', 1)
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')
  await expect(followUpCard(page).locator('.content-q[lang="pl-PL"]')).toHaveText(FOLLOW_UP_TEXT, { timeout: 40_000 })
  await expect(page.locator('.scores')).toHaveCount(0)
  await expect(recording(page)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')

  await expect(page).toHaveURL(/\/rounds\/\d+\/summary$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible()
  // the summary polls until the follow-up's result is in: a factual average and no Provisional chip
  const followUps = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Follow-ups', exact: true }) })
  await expect(followUps.getByText(/^1 asked · average factual \d+ · [01] Good or better$/)).toBeVisible({ timeout: 40_000 })
  await expect(followUps.getByText('Provisional', { exact: true })).toHaveCount(0)
})
