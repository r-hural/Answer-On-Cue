import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_source_policy.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const OBJECTIVE = 'Wyjaśnić cel ostracyzmu'

interface Seeded {
  subject_id: number
  strict_round_id: number
  expanded_round_id: number
  attempt_id: number
}

test.describe('source policy', () => {
  let ids: Seeded

  test.beforeAll(() => {
    const out = execFileSync(python, [seed, `E2E sources ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    ids = JSON.parse(out.trim().split('\n').pop()!) as Seeded
  })

  test('feedback shows the source conflict and what goes beyond the materials', async ({ page }) => {
    await page.goto(`/rounds/${ids.expanded_round_id}/answers/${ids.attempt_id}`)
    const notice = page.locator('.notice').filter({ hasText: 'Your sources disagree on this point' })
    await expect(notice).toBeVisible()
    await expect(notice.locator('.source-quote')).toHaveCount(2)
    await page.getByText('Model answer', { exact: true }).click()
    await expect(page.getByText('Beyond your materials:', { exact: true })).toBeVisible()
  })

  test('history filters by Sources', async ({ page }) => {
    await page.goto('/progress/history')
    await page.locator('#f-subject').selectOption(String(ids.subject_id))
    await expect(page.locator(`a[href="/rounds/${ids.strict_round_id}/summary"]`)).toBeVisible()
    // "All questions" holds the expanded rounds from before the sources rework
    await page.locator('#f-sources').selectOption('all')
    await expect(page.locator('.rows.rounds > li')).toHaveCount(1)
    const row = page.locator(`a[href="/rounds/${ids.expanded_round_id}/summary"]`)
    await expect(row).toBeVisible()
    await expect(page.locator('.rows.rounds > li .meta').first()).toContainText('· My materials + expand')
    await expect(page.locator(`a[href="/rounds/${ids.strict_round_id}/summary"]`)).toHaveCount(0)
  })

  test('coverage shows My materials apart from All questions', async ({ page }) => {
    const row = () => page.getByRole('row').filter({ hasText: OBJECTIVE })
    const sources = page.getByRole('navigation', { name: 'Sources' })
    await page.goto(`/progress/coverage?subject=${ids.subject_id}&layer=materials`)
    await expect(sources.getByRole('link', { name: 'My materials', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('heading', { level: 2, name: 'Coverage map' })).toBeVisible()
    await expect(row()).toContainText('Improving')
    // an old expanded-layer link opens All questions, which counts every source policy
    await page.goto(`/progress/coverage?subject=${ids.subject_id}&layer=expanded`)
    await expect(sources.getByRole('link', { name: 'All questions', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(row()).toBeVisible()
  })

  test('the bank marks the source issue and the mixed question', async ({ page }) => {
    await page.goto(`/library/${ids.subject_id}/questions`)
    const issue = page.getByRole('article', { name: 'Kiedy wprowadzono ostracyzm?', exact: true })
    const notice = issue.locator('.notice').filter({ hasText: 'Your sources may disagree' })
    await expect(notice.getByText('Your sources may disagree', { exact: true })).toBeVisible()
    await expect(notice.locator('blockquote.source-quote')).toHaveCount(2)
    const mixed = page.getByRole('article', { name: 'Wyjaśnij cel ostracyzmu i jego losy w V wieku p.n.e.', exact: true })
    await expect(mixed.locator('.chip', { hasText: /^Mixed$/ })).toBeVisible()
  })

  test('setup reads an old Expand the topic link as All questions', async ({ page }) => {
    await page.goto(`/practice?subject=${ids.subject_id}&sources=expanded`)
    await expect(page.getByRole('radio', { name: /^All questions/ })).toBeChecked()
    await expect(page.locator('.setup-summary')).toContainText('(3 available)')
    await expect(page.locator('.setup-summary')).toContainText('· All questions')
    await page.getByRole('radio', { name: /^My materials/ }).check()
    await expect(page.locator('.setup-summary')).toContainText('(2 available)')
  })
})
