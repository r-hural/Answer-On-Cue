import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

// click_only.wav never starts a recording by itself: every answer here is "I don't know",
// pressed during the preparation. The server runs e2e_app.py; nothing is stubbed in the browser.

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_adaptive.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const dontKnow = (page: Page) => page.getByRole('button', { name: /^I don't know\b/ })

async function throughMicrophone(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/practice\/\d+\/microphone$/)
  await page.getByRole('button', { name: 'Allow microphone', exact: true }).click()
  await expect(page.getByText('The microphone works.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Start the round', exact: true }).click()
  await expect(page).toHaveURL(/\/rounds\/\d+$/)
}

/** Answers each question with "I don't know" once its button can be pressed. */
async function dontKnowAll(page: Page, total: number): Promise<void> {
  for (let n = 1; n <= total; n += 1) {
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(`Question ${n} of ${total}`)
    await expect(dontKnow(page)).toBeEnabled()
    await dontKnow(page).click()
  }
}

test.describe('adaptive', () => {
  let subject: string

  test.beforeAll(() => {
    subject = `E2E adaptive ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    execFileSync(python, [seed, subject], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
  })

  test('an adaptive exam round, its extension apart from the score, and the history', async ({ page }) => {
    test.setTimeout(150_000)

    // Setup: Adaptive, Exam feedback, 4 questions, the extension with 1 extra question.
    await page.goto('/practice')
    await expect(page.getByLabel('Subject', { exact: true })).toBeVisible()
    await page.getByLabel('Subject', { exact: true }).selectOption({ label: subject })
    const adaptive = page.getByRole('radio', { name: /^Adaptive:/ })
    const exam = page.getByRole('radio', { name: /^Exam:/ })
    const followUps = page.getByRole('checkbox', { name: /^Follow-up questions/ })
    const extension = page.getByRole('checkbox', { name: /^Adaptive extension/ })
    const extra = page.getByLabel('Extra questions', { exact: true })
    await adaptive.check()
    await exam.check()
    await page.getByLabel('Main questions', { exact: true }).fill('4')
    await page.getByLabel('Preparation (s)', { exact: true }).fill('10')
    await page.getByLabel('Answer time (s)', { exact: true }).fill('10')
    await page.getByLabel('Read questions aloud', { exact: true }).selectOption('off')
    await followUps.uncheck()
    await extension.check()
    await extra.fill('1')
    await expect(adaptive).toBeChecked()
    await expect(exam).toBeChecked()
    await expect(followUps).not.toBeChecked()
    await expect(extension).toBeChecked()
    await expect(extra).toHaveValue('1')
    await expect(page.getByLabel('Main questions', { exact: true })).toHaveValue('4')
    await expect(page.getByText('All settings work together.', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Check microphone and start', exact: true }).click()
    await throughMicrophone(page)

    // The main round: four times "I don't know".
    await dontKnowAll(page, 4)

    // The summary: the mix, a score of 0 and the offer.
    await expect(page.getByRole('heading', { level: 1, name: 'Round finished' })).toBeVisible({ timeout: 20_000 })
    const parentUrl = page.url()
    await expect(page.locator('.page-head p').last()).toContainText(' · Adaptive · ')
    await expect(
      page.getByText('Chosen from your coverage: 2 Needs work · 1 Not practised · 1 review', { exact: true }),
    ).toBeVisible()
    await expect(page.locator('.headline b')).toHaveText('0')
    const keep = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Keep practising', exact: true }) })
    await expect(keep).toContainText("1 new question on this round's weak topics")
    const start = page.getByRole('button', { name: 'Start 1 extra question', exact: true })
    await expect(start).toBeEnabled()
    await start.click()

    // The extension: its own round of one question.
    await throughMicrophone(page)
    await dontKnowAll(page, 1)
    await expect(page.getByRole('heading', { level: 1, name: 'Adaptive extension finished' })).toBeVisible({ timeout: 20_000 })
    await page.getByRole('link', { name: 'Back to the round', exact: true }).click()
    await expect(page).toHaveURL(parentUrl)
    const box = page.locator('section[aria-labelledby="h-extension"]')
    await expect(box).toContainText('1 asked')
    await expect(page.locator('.headline b')).toHaveText('0')

    // History: one row for this round (the extension is not listed), labelled Adaptive. The seed's
    // day-old practice rounds of this subject may also be among History's newest 50 rows, depending
    // on what else the shared e2e database holds, so only rows other than those are counted.
    await page.goto('/progress/history')
    const rows = page
      .locator('.rows.rounds > li')
      .filter({ has: page.getByText(subject, { exact: true }) })
      .filter({ hasNotText: 'Standard · Practice' })
    await expect(rows).toHaveCount(1)
    await expect(rows.first().locator('.meta')).toContainText('Adaptive · Exam')
  })
})
