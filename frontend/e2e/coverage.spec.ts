import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_coverage.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const TOPIC = 'Ostracyzm: procedura i cel'

test.describe('coverage', () => {
  let subjectId: number
  let topicId: number

  test.beforeAll(() => {
    const out = execFileSync(python, [seed, `E2E coverage ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    const ids = JSON.parse(out.trim().split('\n').pop()!) as { subject_id: number; topic_id: number }
    subjectId = ids.subject_id
    topicId = ids.topic_id
  })

  test('the coverage map, a topic with its evidence, and the subject box', async ({ page }) => {
    await page.goto(`/progress/coverage?subject=${subjectId}`)
    await expect(page.getByRole('heading', { level: 1, name: 'Progress' })).toBeVisible()

    const cell = (status: string) => page.locator('.scores > div').filter({ hasText: status })
    await expect(cell('Strong').locator('.v')).toHaveText('1')
    await expect(cell('Strong')).toContainText('of 4 learning objectives')
    await expect(cell('Improving').locator('.v')).toHaveText('1')
    await expect(cell('Needs work').locator('.v')).toHaveText('1')
    await expect(cell('Not practised').locator('.v')).toHaveText('1')

    const row = (objective: string) => page.getByRole('row').filter({ hasText: objective })
    await expect(row('Opisać procedurę ostracyzmu')).toContainText('Needs work')
    await expect(row('Opisać procedurę ostracyzmu')).toContainText('51')
    await expect(row('Wyjaśnić cel polityczny ostracyzmu')).toContainText('Strong')
    await expect(row('Wyjaśnić cel polityczny ostracyzmu')).toContainText('84 · 88')
    await expect(row('Ocenić znaczenie reform')).toContainText('Not practised')
    await expect(row('Ocenić znaczenie reform')).toContainText('—')

    await page.getByRole('link', { name: TOPIC, exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/progress/topics/${topicId}$`))
    await expect(page.getByRole('heading', { level: 1, name: TOPIC })).toBeVisible()
    await expect(page.locator('ul.rows > li')).toHaveCount(2)
    await expect(page.locator('table.wide tbody tr')).toHaveCount(3)
    await expect(page.locator('table.wide tbody tr').first().getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      /^\/rounds\/\d+\/answers\/\d+$/,
    )
    await expect(page.locator('blockquote.source-quote').first()).toContainText('version 1')

    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Progress' }).click()
    await expect(page).toHaveURL(new RegExp(`/progress/coverage\\?subject=${subjectId}$`))
    await expect(page.getByRole('heading', { level: 1, name: 'Progress' })).toBeVisible()

    await page.goto(`/library/${subjectId}`)
    const box = page.locator('section.box.tint').filter({ has: page.getByRole('heading', { name: 'Coverage' }) })
    await expect(box.locator('dl.facts')).toContainText(/Needs work\s*1 topic/)
    await expect(box.locator('dl.facts')).toContainText(/Improving\s*1 topic/)
  })
})
