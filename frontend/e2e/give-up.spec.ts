import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_give_up.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

interface Seeded {
  subject_id: number
  round_id: number
  failed_attempt_id: number
}

// The e2e database is shared with the other specs: this one seeds its own subject and round.
test.describe('giving up an answer', () => {
  let name: string
  let ids: Seeded

  test.beforeEach(() => {
    name = `E2E give up ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`
    const out = execFileSync(python, [seed, name], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    ids = JSON.parse(out.trim().split('\n').pop()!) as Seeded
  })

  test('a failed answer is given up and leaves the score', async ({ page, request }) => {
    await page.goto(`/rounds/${ids.round_id}/summary`)
    const row = page.locator('ul.rows > li').filter({ hasText: 'Na czym polegał ostracyzm' })
    await expect(row.getByText('Unresolved')).toBeVisible()
    await expect(row.getByRole('button', { name: 'Try again' })).toBeVisible()
    await expect(page.getByText('Provisional results.')).toBeVisible()

    await row.getByRole('button', { name: 'Give up this answer' }).click()
    await expect(row.getByText("It is left out of the score as not assessed, and it can't be tried again.")).toBeVisible()
    await row.getByRole('button', { name: 'Give up', exact: true }).click()

    await expect(row.getByText('Not assessed', { exact: true })).toBeVisible()
    await expect(row.getByText('You gave up this answer. It is not part of the score.')).toBeVisible()
    await expect(page.getByText('Provisional results.')).toHaveCount(0)
    await expect(page.getByText('1 answer not assessed (left out of the score)')).toBeVisible()

    await row.getByRole('link', { name: 'Feedback' }).click()
    await expect(page).toHaveURL(new RegExp(`/rounds/${ids.round_id}/answers/${ids.failed_attempt_id}$`))
    await expect(page.getByText('Not assessed.')).toBeVisible()
    await expect(page.locator('.band')).toHaveCount(0)

    const summary = await request.get(`/api/rounds/${ids.round_id}/summary`)
    expect(summary.ok()).toBe(true)
    expect((await summary.json()).provisional).toBe(false)

    await page.goto('/progress/history')
    const history = page.locator('li').filter({ hasText: name })
    await expect(history).toHaveCount(1)
    await expect(history.locator('dd.score')).not.toHaveText('—')
    await expect(history.getByText('provisional')).toHaveCount(0)
  })
})

test.describe('giving up an answer during the round', () => {
  let ids: Seeded

  test.beforeEach(() => {
    const name = `E2E give up in round ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`
    const out = execFileSync(python, [seed, name, 'in-round'], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    ids = JSON.parse(out.trim().split('\n').pop()!) as Seeded
  })

  // "Try again" is never clicked: the seeded answer has no audio to run on.
  test('a failed answer is given up on the round screen', async ({ page, request }) => {
    await page.goto(`/rounds/${ids.round_id}`)
    await expect(page.getByText('This answer could not be processed.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
    await expect(page.getByRole('button', { name: /^Continue/ })).toBeVisible()

    await page.getByRole('button', { name: 'Give up this answer' }).click()
    await expect(page.getByText("It is left out of the score as not assessed, and it can't be tried again.")).toBeVisible()
    await page.getByRole('button', { name: 'Give up', exact: true }).click()

    await expect(page.getByText('Not assessed.')).toBeVisible()
    await expect(page.locator('.band')).toHaveCount(0)
    const results = page.getByRole('button', { name: /See the round results/ })
    await expect(results).toBeVisible()

    await results.click()
    await expect(page).toHaveURL(new RegExp(`/rounds/${ids.round_id}/summary$`))
    const row = page.locator('ul.rows > li').filter({ hasText: 'Na czym polegał ostracyzm' })
    await expect(row.getByText('Not assessed', { exact: true })).toBeVisible()
    await expect(page.getByText('Provisional results.')).toHaveCount(0)

    const summary = await request.get(`/api/rounds/${ids.round_id}/summary`)
    expect(summary.ok()).toBe(true)
    const body = await summary.json()
    expect(body.provisional).toBe(false)
    expect(body.outcomes.given_up).toBe(1)
  })
})
