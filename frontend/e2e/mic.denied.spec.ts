import { expect, test } from '@playwright/test'

test('a denied microphone is explained in English', async ({ page }) => {
  await page.goto('/microphone')
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByRole('alert')).toHaveText(
    'Microphone access is blocked. Allow the microphone in your browser and reload the page.',
  )
  await expect(page.getByRole('meter')).toHaveCount(0)
})
