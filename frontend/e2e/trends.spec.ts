import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_trends.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

test.describe('trends', () => {
  let name: string
  let subjectId: number
  let rounds: number[]
  let expandedRound: number

  test.beforeAll(() => {
    name = `E2E trends ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const out = execFileSync(python, [seed, name], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    const ids = JSON.parse(out.trim().split('\n').pop()!) as {
      subject_id: number
      round_ids: number[]
      expanded_round_id: number
    }
    subjectId = ids.subject_id
    rounds = ids.round_ids
    expandedRound = ids.expanded_round_id
  })

  test('lines per round, answers with help only on request, the sources groups apart', async ({ page }) => {
    // the materials lines; Trends opens on All questions, which also holds the expanded round
    await page.goto(`/progress/trends?subject=${subjectId}&sources=materials`)
    await expect(page.getByRole('heading', { level: 1, name: 'Progress' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Trends', exact: true })).toHaveAttribute('aria-current', 'page')

    const knowledge = page.getByRole('group', { name: /^Knowledge and language per round/ })
    await expect(knowledge).toHaveAttribute('aria-label', /Factual: 45, 55, 75/)

    const help = page.getByRole('checkbox', { name: 'Include answers with help', exact: true })
    await help.check()
    await expect(knowledge).toHaveAttribute('aria-label', /Factual: 45, 55, 83/)
    await help.uncheck()
    await expect(knowledge).toHaveAttribute('aria-label', /Factual: 45, 55, 75/)

    await expect(page.locator(`a[href="/rounds/${expandedRound}/summary"]`)).toHaveCount(0)
    await page.getByLabel('Sources', { exact: true }).selectOption('all')
    await expect(page.locator(`a[href="/rounds/${expandedRound}/summary"]`).first()).toBeVisible()

    await page.getByLabel('Sources', { exact: true }).selectOption('materials')
    await expect(knowledge).toBeVisible()
    await expect(page.locator(`a[href="/rounds/${expandedRound}/summary"]`)).toHaveCount(0)
    await page.locator(`a[href="/rounds/${rounds[2]}/summary"]`).first().click()
    await expect(page).toHaveURL(new RegExp(`/rounds/${rounds[2]}/summary$`))
  })
})
