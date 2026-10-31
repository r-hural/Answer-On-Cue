import { expect, test } from '@playwright/test'

// The real PUT /api/data/cleanup writes the shared settings.env, so this spec stubs it; the
// pruning itself is covered by the backend tests.
test('the clean-up limits are shown and saved', async ({ page }) => {
  const bodies: unknown[] = []
  let caught = 0
  let sent = 0
  page.on('request', (request) => {
    if (request.method() === 'PUT' && new URL(request.url()).pathname === '/api/data/cleanup') sent += 1
  })
  await page.route('**/api/data/cleanup', async (route) => {
    if (route.request().method() === 'PUT') caught += 1
    bodies.push(route.request().postDataJSON())
    await route.fulfill({
      json: { pre_restore_keep: 3, export_keep_days: 14, deleted_backups: 2, deleted_exports: 0 },
    })
  })

  await page.goto('/settings#storage')
  const section = page.locator('section#storage')
  await expect(section.getByRole('heading', { level: 3, name: 'Automatic clean-up' })).toBeVisible()
  const keep = section.getByLabel('Pre-restore backups to keep')
  const days = section.getByLabel('Delete exports after (days)')
  await expect(keep).toHaveValue('5')
  await expect(days).toHaveValue('30')
  const save = section.getByRole('button', { name: 'Save clean-up' })
  await expect(save).toBeDisabled()

  await keep.fill('3')
  await days.fill('14')
  await save.click()

  await expect(section.getByRole('status').filter({ hasText: 'Clean-up saved.' })).toHaveText(
    'Clean-up saved. Deleted 2 old pre-restore backups.',
  )
  expect(bodies).toEqual([{ pre_restore_keep: 3, export_keep_days: 14 }])
  expect(caught).toBe(1)
  expect(sent).toBe(1)
})
