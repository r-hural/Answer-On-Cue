import { expect, test, type Page } from '@playwright/test'

// speech_mid.wav loops: 2.0 s silence, speech 2.0-5.51 s, 2.0 s silence (7.51 s per loop).
// The server runs e2e_app.py: the real pipeline with a fake transcriber and a fake assessor, so
// nothing is stubbed in the browser except where a test says so.

const recording = (page: Page) => page.locator('.live', { hasText: 'Recording' })

/** Sets up a practice round of 2 questions of the seeded subject and opens the exam screen. */
async function startRound(page: Page): Promise<void> {
  await page.goto('/practice')
  await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
  await page.getByLabel('Subject', { exact: true }).selectOption({ label: 'E2E exam' })
  await page.getByRole('radio', { name: /^Practice/ }).check()
  await page.getByLabel('Main questions').fill('2')
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

test('a practice round through the real pipeline ends in a summary and the history', async ({ page }) => {
  test.setTimeout(150_000)
  await startRound(page)

  // Question 1: speech starts the recording by itself; Space finishes it.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2')
  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2: feedback', { timeout: 40_000 })
  await expect(page.locator('.scores .band')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What was correct' })).toBeVisible()
  await expect(page.locator('.transcript[lang="pl-PL"]')).toContainText('Ostracyzm to było głosowanie na skorupach')
  await expect(page.locator('.player')).toBeVisible()
  await page.keyboard.press('Enter')

  // Question 2: N is "I don't know"; the model answer opens by itself.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2')
  // The looping fixture may already have started the recording by speech; N works in both.
  await expect(page.getByText('Listening', { exact: true }).or(recording(page))).toBeVisible()
  await page.keyboard.press('n')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2: feedback')
  await expect(page.getByRole('heading', { level: 2, name: 'Model answer' })).toBeVisible()
  await page.getByRole('button', { name: /See the round results/ }).click()

  await expect(page).toHaveURL(/\/rounds\/\d+\/summary$/)
  const roundId = /\/rounds\/(\d+)\/summary$/.exec(page.url())![1]
  await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible()
  await expect(page.locator('.headline b')).toHaveText(/^\d+$/)
  await expect(page.locator('.band-list')).toContainText('1 Weak')

  // The round is listed in the history; its row is found by its link, other specs add rounds too.
  await page.getByRole('link', { name: 'Progress' }).click()
  await expect(page).toHaveURL(/\/progress\/history$/)
  await expect(page.locator(`a[href="/rounds/${roundId}/summary"]`)).toBeVisible()
})

test('a failed upload is named and never scored', async ({ page }) => {
  test.setTimeout(120_000)
  await page.route('**/api/attempts/*/audio', (route) => route.abort())
  await startRound(page)

  await expect(recording(page)).toBeVisible({ timeout: 12_000 })
  await expect(page.getByRole('button', { name: /Finish answer/ })).toBeEnabled()
  await page.keyboard.press('Space')

  // The upload queue retries after 1, 3 and 9 seconds before it gives up.
  await expect(page.getByText(/An answer could not be uploaded yet/)).toBeVisible({ timeout: 40_000 })
  await expect(page.getByText(/has not reached the server yet, so it cannot be scored/)).toBeVisible()
  await expect(page.locator('.band')).toHaveCount(0)
  await expect(page.locator('.scores')).toHaveCount(0)
})
