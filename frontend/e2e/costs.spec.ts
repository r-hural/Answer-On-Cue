import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_costs.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

const usd = (value: number) => `$${value.toFixed(2)}`

// The e2e database is shared with the other specs: this one reads its own seeded subject and
// never changes a global setting. The ceiling and the over-budget estimate are stubbed with
// page.route; PUT /api/settings is fulfilled here and never reaches the server.
test.describe('cost controls', () => {
  let name: string
  let subjectId: number
  let roundId: number
  let roundCost: number

  test.beforeAll(() => {
    name = `E2E costs ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const out = execFileSync(python, [seed, name], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    const ids = JSON.parse(out.trim().split('\n').pop()!) as {
      subject_id: number
      round_id: number
      round_cost_usd: number
    }
    subjectId = ids.subject_id
    roundId = ids.round_id
    roundCost = ids.round_cost_usd
  })

  test('usage by subject and round, the cost in History', async ({ page }) => {
    await page.goto('/settings/usage')
    await expect(page.getByRole('heading', { level: 1, name: 'AI usage' })).toBeVisible()
    const tabs = page.getByRole('navigation', { name: 'Group by' })
    await tabs.getByRole('link', { name: 'By subject', exact: true }).click()
    await expect(tabs.getByRole('link', { name: 'By subject', exact: true })).toHaveAttribute('aria-current', 'page')
    const subjectRow = page.getByRole('row').filter({ has: page.getByText(name, { exact: true }) })
    await expect(subjectRow).toHaveCount(1)
    const cells = subjectRow.getByRole('cell')
    await expect(cells.nth(1)).toHaveText('3')
    await expect(cells.nth(4)).toHaveText(usd(roundCost))

    await tabs.getByRole('link', { name: 'By round', exact: true }).click()
    const roundRow = page.getByRole('row').filter({ has: page.getByRole('link', { name: `Round ${roundId}`, exact: true }) })
    await expect(roundRow).toHaveCount(1)
    await expect(roundRow.getByRole('cell').nth(4)).toHaveText(usd(roundCost))

    // The history lists the newest rounds of every spec; this round is backdated, so the list is
    // narrowed to its own subject before it is looked for.
    await page.goto('/progress/history')
    const subjectFilter = page.getByLabel('Subject', { exact: true })
    await expect(subjectFilter.locator(`option[value="${subjectId}"]`)).toHaveCount(1)
    await subjectFilter.selectOption(String(subjectId))
    const item = page.getByRole('listitem').filter({ has: page.getByText(name, { exact: true }) })
    await expect(item).toHaveCount(1)
    const cost = item.locator('dt', { hasText: /^AI cost$/ })
    await expect(cost).toBeVisible()
    await expect(item.locator('dd').last()).toHaveText(usd(roundCost))
  })

  test('Settings → Spending saves the ceiling (stubbed, the server keeps its own)', async ({ page }) => {
    let preferences: Record<string, unknown> | null = null
    const sent: unknown[] = []
    await page.route('**/api/costs/summary*', (route) =>
      route.fulfill({
        json: {
          month: { month: '2026-10', ceiling_usd: 15, spent_usd: 2.4, reserved_usd: 0, remaining_usd: 12.6 },
          rounds: 6,
          per_round_usd: 0.31,
          library_usd: 0.54,
          voice_usd: 0,
          voice_provider: 'piper',
          blocked: 0,
          months: ['2026-10'],
        },
      }),
    )
    await page.route('**/api/settings', async (route) => {
      if (route.request().method() === 'GET') {
        const response = await route.fetch()
        preferences = { ...(await response.json()), spending_ceiling_usd: 15 }
        return route.fulfill({ json: preferences })
      }
      const patch = route.request().postDataJSON() as Record<string, unknown>
      sent.push(patch)
      preferences = { ...preferences, ...patch }
      return route.fulfill({ json: preferences })
    })
    await page.goto('/settings#spending')
    const section = page.locator('section#spending')
    await expect(section.getByText('$2.40 of $15.00')).toBeVisible()
    await expect(section.getByRole('img', { name: '16% of the monthly ceiling used' })).toBeVisible()
    const field = section.getByLabel('Monthly ceiling for AI usage (USD)')
    await expect(field).toHaveValue('15')
    await field.fill('20')
    const save = section.getByRole('button', { name: 'Save', exact: true })
    await expect(save).toBeEnabled()
    await save.click()
    await expect(section.getByText('Spending ceiling saved.')).toBeVisible()
    expect(sent).toEqual([{ spending_ceiling_usd: 20 }])
  })

  test('Practice setup shows the estimate; over budget it cannot start', async ({ page }) => {
    await page.goto(`/practice?subject=${subjectId}`)
    const summary = page.getByRole('complementary', { name: 'This round' })
    await expect(summary.locator('dt', { hasText: /^Estimated AI cost$/ })).toBeVisible()
    await expect(summary.locator('dd strong.num')).toHaveText(/^\$\d+\.\d{2,4}(–\d+\.\d{2,4})?$/)

    await page.route('**/api/costs/estimate/round*', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      return route.fulfill({
        json: {
          ...body,
          fits: false,
          month: { ...body.month, ceiling_usd: 15, spent_usd: 14.82, remaining_usd: 0.18 },
        },
      })
    })
    const count = page.getByLabel('Main questions')
    await expect(count).toBeEnabled()
    await count.fill('1')
    await count.blur()
    await expect(summary.getByText('The monthly spending ceiling is reached.', { exact: true })).toBeVisible()
    await expect(summary.getByRole('link', { name: 'Change the ceiling', exact: true })).toHaveAttribute('href', '/settings#spending')
    await expect(page.getByRole('button', { name: 'Check microphone and start', exact: true })).toBeDisabled()
  })
})
