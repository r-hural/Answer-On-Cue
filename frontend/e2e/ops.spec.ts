import { expect, test } from '@playwright/test'
import fs from 'node:fs'

const sentence = 'Demokracja ateńska opierała się na zgromadzeniu ludowym i radzie pięciuset. '
const paragraph = (extra: string) => `${extra} ${sentence.repeat(13)}`.trim()

// The last passages come after a "Bibliografia" line, so the back-matter suggestion applies.
const document = [
  paragraph('Wstęp.'),
  paragraph('Ustrój.'),
  'Bibliografia',
  paragraph('1. Bravo, Wipszycka, Historia starożytnych Greków.'),
  paragraph('2. Hansen, Polis i państwo-miasto.'),
  paragraph('3. Ober, Demokracja ateńska.'),
].join('\n\n')

test('a setting saved in the browser survives a reload', async ({ page }) => {
  await page.goto('/settings')
  const correctness = page.getByLabel('Correctness', { exact: true })
  await correctness.fill('40')
  await expect(page.getByLabel('Completeness', { exact: true })).toHaveValue('40')
  await page.getByRole('button', { name: 'Save scoring' }).click()
  await expect(page.getByText('Scoring saved.')).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('Correctness', { exact: true })).toHaveValue('40')
  await expect(page.getByLabel('Completeness', { exact: true })).toHaveValue('40')

  // Back to the default so a rerun starts from the same state.
  await page.getByLabel('Correctness', { exact: true }).fill('30')
  await page.getByRole('button', { name: 'Save scoring' }).click()
  await expect(page.getByText('Scoring saved.')).toBeVisible()
})

test('an uploaded document gets the back-matter suggestion, accepted by the user', async ({
  page,
}, testInfo) => {
  const file = testInfo.outputPath('ateny-bibliografia.txt')
  fs.writeFileSync(file, document, 'utf-8')

  await page.goto('/library')
  const name = `E2E ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  await page.getByLabel('Subject name').fill(name)
  await page.getByLabel('Content language').selectOption('pl')
  await page.getByRole('button', { name: 'Add subject' }).click()
  await page.getByRole('link', { name }).click()

  await page.getByLabel('Document file').setInputFiles(file)
  const banner = page.getByRole('region', { name: 'Footnotes and bibliography' })
  await expect(banner).toContainText('The end of the document looks like footnotes')
  await banner.getByRole('button', { name: 'Exclude' }).click()
  await expect(banner).toContainText('Excluded 3 passages.')
})
