import { expect, test } from '@playwright/test'
import { waitForLevelAbove } from './mic.helpers'

// click_only.wav loops: 3 s of silence with three 60 ms full-scale clicks.
test('clicks never auto-start; recording starts by timer', async ({ page }) => {
  test.setTimeout(60_000)
  await page.goto('/microphone')
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByText('The microphone works.')).toBeVisible()

  // The clicks really arrive (otherwise this test would prove nothing).
  await waitForLevelAbove(page, -40)

  await page.getByRole('button', { name: 'Calibrate silence' }).click()
  await expect(page.getByText(/Noise level: −\d+ dB/)).toBeVisible({ timeout: 5000 })

  await page.getByRole('button', { name: 'Listening test' }).click()
  const timer = page.getByRole('timer', { name: 'Preparation' })
  await expect(timer).toBeVisible()
  await waitForLevelAbove(page, -40)

  // Review Focus 1: through the whole 10 s countdown, no click starts the recording.
  // A click that auto-started would show "Speech detected" and never "Preparation over"; waiting for
  // the 1 s reading itself is racy on a busy machine (it lasts one second).
  await expect(page.getByText('Preparation over — recording')).toBeVisible({ timeout: 14_000 })
  await expect(page.getByText('Speech detected — recording')).toHaveCount(0)
})
