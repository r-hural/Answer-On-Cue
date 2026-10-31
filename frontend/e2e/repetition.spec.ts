import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

// click_only.wav never starts a recording by itself: every answer here is "I don't know",
// pressed during the preparation. The server runs e2e_app.py; nothing is stubbed in the browser.

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_repetition.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const dontKnow = (page: Page) => page.getByRole('button', { name: /^I don't know\b/ })
const nextQuestion = (page: Page) => page.getByRole('button', { name: /^Next question\b/ })
const results = (page: Page) => page.getByRole('button', { name: /^See the round results\b/ })
const againHeading = (page: Page) => page.getByRole('heading', { level: 2, name: 'Answer again', exact: true })

async function throughMicrophone(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/practice\/\d+\/microphone$/)
  await page.getByRole('button', { name: 'Allow microphone', exact: true }).click()
  await expect(page.getByText('The microphone works.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Start the round', exact: true }).click()
  await expect(page).toHaveURL(/\/rounds\/\d+$/)
}

/** "I don't know" once its button can be pressed. */
async function sayDontKnow(page: Page): Promise<void> {
  await expect(dontKnow(page)).toBeEnabled()
  await dontKnow(page).click()
}

test.describe('repetition', () => {
  let subject: string

  test.beforeAll(() => {
    subject = `E2E repetition ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    execFileSync(python, [seed, subject], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
  })

  test('answer again apart from the score, a retry round of the answers below Good, and the history', async ({ page }) => {
    test.setTimeout(150_000)

    // Setup: Practice feedback, 2 questions, no follow-ups; Answer again and Retry round on by default.
    await page.goto('/practice')
    await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
    await page.getByLabel('Subject', { exact: true }).selectOption({ label: subject })
    const practice = page.getByRole('radio', { name: /^Practice:/ })
    const followUps = page.getByRole('checkbox', { name: /^Follow-up questions/ })
    const again = page.getByRole('checkbox', { name: /^Answer again/ })
    const retry = page.getByRole('checkbox', { name: /^Retry round/ })
    const extension = page.getByRole('checkbox', { name: /^Adaptive extension/ })
    await practice.check()
    await page.getByLabel('Main questions', { exact: true }).fill('2')
    await page.getByLabel('Preparation (s)', { exact: true }).fill('10')
    await page.getByLabel('Answer time (s)', { exact: true }).fill('10')
    await page.getByLabel('Read questions aloud', { exact: true }).selectOption('off')
    await followUps.uncheck()
    await expect(practice).toBeChecked()
    await expect(followUps).not.toBeChecked()
    await expect(again).toBeChecked()
    await expect(retry).toBeChecked()
    await expect(extension).not.toBeChecked()
    await expect(page.getByLabel('Main questions', { exact: true })).toHaveValue('2')
    await expect(page.getByText('All settings work together.', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Check microphone and start', exact: true }).click()
    await throughMicrophone(page)

    // Question 1: "I don't know", then Answer again, then "I don't know" once more.
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 1 of 2')
    await sayDontKnow(page)
    await expect(againHeading(page)).toBeVisible({ timeout: 20_000 })
    await page.getByRole('button', { name: 'Answer again', exact: true }).click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('answer again')
    await sayDontKnow(page)
    await expect(page.getByRole('heading', { level: 2, name: 'First answer and second answer', exact: true })).toBeVisible({
      timeout: 20_000,
    })
    await expect(againHeading(page)).toHaveCount(0)
    await expect(nextQuestion(page)).toBeEnabled()
    await nextQuestion(page).click()

    // Question 2: "I don't know", then the results.
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Question 2 of 2')
    await sayDontKnow(page)
    await expect(results(page)).toBeEnabled({ timeout: 20_000 })
    await results(page).click()

    // The summary: a score of 0, the first row answered again, the retry round as the one primary.
    await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible({ timeout: 20_000 })
    const parentUrl = page.url()
    await expect(page.locator('.headline b')).toHaveText('0')
    await expect(page.locator('.rows > li').first().locator('.meta')).toContainText('Answered again:')
    const start = page.getByRole('button', { name: 'Start retry round (2)', exact: true })
    await expect(start).toBeEnabled({ timeout: 20_000 })
    await expect(page.locator('.btn.primary')).toHaveCount(1)
    await expect(start).toHaveClass(/\bprimary\b/)
    await start.click()

    // The retry round: the same two questions, "I don't know" each.
    await throughMicrophone(page)
    for (const [n, last] of [
      [1, false],
      [2, true],
    ] as const) {
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(`Question ${n} of 2`)
      await sayDontKnow(page)
      const button = last ? results(page) : nextQuestion(page)
      await expect(button).toBeEnabled({ timeout: 20_000 })
      await expect(againHeading(page)).toHaveCount(0) // a retry round offers no Answer again
      await button.click()
    }

    // The retry summary: its score next to the original's, still below Good.
    await expect(page.getByRole('heading', { level: 1, name: 'Retry round finished' })).toBeVisible({ timeout: 20_000 })
    const original = page.locator('.scores > div').filter({ hasText: 'Same questions, original round' })
    await expect(original.locator('.v')).toHaveText('0')
    const still = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Still below Good', exact: true }) })
    await expect(still.locator('ul.list > li')).toHaveCount(2)

    // Back to the original round: the retry box links the retry round; the score is still 0.
    await page.getByRole('link', { name: 'Original round', exact: true }).click()
    await expect(page).toHaveURL(parentUrl)
    const box = page.locator('section[aria-labelledby="h-retry"]')
    await expect(box.getByRole('link')).toHaveAttribute('href', /\/rounds\/\d+\/summary$/)
    await expect(page.locator('.headline b')).toHaveText('0')

    // History: the round and its retry round, each with its own row.
    await page.goto('/progress/history')
    const rows = page.locator('.rows.rounds > li').filter({ has: page.getByText(subject, { exact: true }) })
    await expect(rows).toHaveCount(2)
    await expect(rows.filter({ hasText: /Retry round of \d\d:\d\d/ })).toHaveCount(1)
  })
})
