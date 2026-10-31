import { expect, test } from '@playwright/test'

test('home page shows its heading', async ({ page }) => {
  await page.goto('/')
  // The e2e server may or may not see an API key, so either Home variant is right.
  await expect(
    page.getByRole('heading', { level: 1, name: /^(Home|Welcome to AnswerOnCue)$/ }),
  ).toBeVisible()
})
