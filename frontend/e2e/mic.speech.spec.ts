import { expect, test } from '@playwright/test'
import { waitForLoudThenQuiet } from './mic.helpers'

// speech_mid.wav loops: 2.0 s silence, speech 2.0–5.51 s, 2.0 s silence (7.51 s per loop).
test('speech after silence auto-starts and the recording keeps the pre-roll', async ({ page, request }) => {
  test.setTimeout(90_000)
  await page.goto('/microphone')
  await page.getByRole('button', { name: 'Allow microphone' }).click()
  await expect(page.getByText('The microphone works.')).toBeVisible()
  const resampled = await page.getByText('Your browser does not support 16 kHz', { exact: false }).count()
  console.log(`mic-speech AudioContext 16 kHz honoured: ${resampled === 0}`)

  // Calibrate inside the 4 s silent gap between two loops.
  await waitForLoudThenQuiet(page)
  await page.getByRole('button', { name: 'Calibrate silence' }).click()
  await expect(page.getByText(/Noise level: −\d+ dB/)).toBeVisible({ timeout: 5000 })

  // Start listening at the beginning of the next gap, so speech comes ~4 s later.
  await waitForLoudThenQuiet(page, 200)
  await page.getByRole('button', { name: 'Listening test' }).click()
  await expect(page.getByRole('timer', { name: 'Preparation' })).toBeVisible()
  await expect(page.getByText('Speech detected — recording')).toBeVisible({ timeout: 6000 })
  await expect(page.getByText(/^●\s*Recording$/)).toBeVisible()

  const uploaded = page.waitForResponse((r) => r.url().endsWith('/api/recordings') && r.request().method() === 'POST')
  await page.waitForTimeout(4500)
  await page.getByRole('button', { name: 'Stop' }).click()
  const upload = await uploaded
  expect(upload.status()).toBe(201)
  const { id, duration_ms } = await upload.json()
  expect(duration_ms).toBeGreaterThan(4500)

  await expect(page.getByText(/Speech from \d+\.\d s to \d+\.\d s/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByLabel('Play back the recording')).toBeVisible()

  const analyzed = await request.post(`/api/recordings/${id}/analyze`)
  expect(analyzed.status()).toBe(200)
  const vad = await analyzed.json()
  console.log(`mic-speech VAD: ${JSON.stringify(vad)}`)
  expect(vad.speech_intervals.length).toBeGreaterThanOrEqual(1)
  expect(vad.speech_ms).toBeGreaterThan(2500)
  // Review Focus 2: at most the 1 s pre-roll (plus a frame or two) before the first words.
  expect(vad.leading_silence_ms).toBeLessThan(1100)
  expect(vad.leading_silence_ms).toBeGreaterThan(500)
})
