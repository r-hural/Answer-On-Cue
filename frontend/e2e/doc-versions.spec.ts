import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

/**
 * Replacing a document against the real backend (no stubs): seed_doc_versions.py adds a subject with
 * one six-paragraph text document, a topic on all of it, Q1 citing paragraph 2, Q2 citing paragraph 4,
 * and a finished round that answered Q2. Version 2 rewords paragraph 4 and appends a paragraph.
 */

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_doc_versions.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

interface Seeded {
  subject_id: number
  document_id: number
  round_id: number
  q1: number
  q2: number
  text: string
}

const REWORDED = 'Ostracyzm umożliwiał wygnanie z Aten polityka, którego uznano za groźnego dla ustroju.'
const APPENDED = 'Archonci pełnili najwyższe urzędy w Atenach i byli wybierani co roku przez losowanie.'

test('a replaced document marks the question citing it; keeping it leaves the history alone', async ({ page }) => {
  const name = `E2E versions ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const out = execFileSync(python, [seed, name], {
    cwd: backend,
    env: { ...process.env, AOC_DATA_DIR: dataDir },
    encoding: 'utf-8',
  })
  const seeded = JSON.parse(out.trim().split('\n').pop()!) as Seeded
  const paragraphs = seeded.text.split('\n\n')
  const oldFourth = paragraphs[3]
  paragraphs[3] = Array(9).fill(REWORDED).join(' ')
  paragraphs.push(Array(9).fill(APPENDED).join(' '))
  const second = { name: 'demokracja-2.txt', mimeType: 'text/plain', buffer: Buffer.from(paragraphs.join('\n\n')) }

  // 1. the subject page → the document → Replace → What changes
  await page.goto(`/library/${seeded.subject_id}`)
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  const open = page.getByRole('button', { name: 'Open Demokracja ateńska', exact: true })
  await expect(open).toBeEnabled()
  await open.click()
  const replace = page.getByRole('link', { name: 'Replace with a new version', exact: true })
  await expect(replace).toBeVisible()
  await replace.click()
  await expect(page.getByRole('heading', { level: 1, name: 'Replace with a new version' })).toBeVisible()
  await page.getByLabel('Document file', { exact: true }).setInputFiles(second)
  const table = page.locator('table.table')
  await expect(table.getByRole('row', { name: 'Passages unchanged 5', exact: true })).toBeVisible()
  await expect(table.getByRole('row', { name: 'Passages changed 1', exact: true })).toBeVisible()
  await expect(table.getByRole('row', { name: 'Passages removed 0', exact: true })).toBeVisible()
  await expect(table.getByRole('row', { name: 'Passages new 1', exact: true })).toBeVisible()
  await expect(
    table.getByRole('row', { name: 'Questions that cite changed or removed passages 1', exact: true }),
  ).toBeVisible()

  // 2. create → the bank on Needs review → keep
  const create = page.getByRole('button', { name: 'Create version 2 and review the 1 question', exact: true })
  await expect(create).toBeEnabled()
  await create.click()
  const reviewTab = page.getByRole('link', { name: 'Needs review (1)', exact: true })
  await expect(reviewTab).toHaveAttribute('aria-current', 'page')
  const card = page.getByRole('article', { name: 'Na czym polegał ostracyzm w Atenach?', exact: true })
  await expect(card.getByRole('heading', { name: 'Passage in version 1 (used now)', exact: true })).toBeVisible()
  await expect(card.getByRole('heading', { name: 'Passage in version 2', exact: true })).toBeVisible()
  await expect(card.locator('mark').first()).toBeVisible()
  const keep = card.getByRole('button', { name: 'Keep (still correct)', exact: true })
  await expect(keep).toBeEnabled()
  await keep.click()
  await expect(page.getByRole('link', { name: 'Needs review (0)', exact: true })).toBeVisible()
  await expect(card).toHaveCount(0)

  // 3. history → the seeded round → its answer still shows Q2 and the old paragraph 4
  await page.goto('/progress/history')
  const row = page.getByRole('listitem').filter({ hasText: name })
  const roundLink = row.locator('a.title')
  await expect(roundLink).toBeVisible()
  await roundLink.click()
  const feedback = page.getByRole('link', { name: 'Feedback', exact: true })
  await expect(feedback).toBeVisible()
  await feedback.click()
  await expect(page.getByText('Na czym polegał ostracyzm w Atenach?', { exact: true })).toBeVisible()
  const sources = page.getByRole('complementary', { name: 'Sources', exact: true })
  await expect(sources).toContainText(oldFourth.slice(0, 60))
  await expect(sources).not.toContainText('Ostracyzm umożliwiał')

  // 4. the subject page: no notice any more, the document reads Version 2 of 2
  await page.goto(`/library/${seeded.subject_id}?document=${seeded.document_id}`)
  await expect(page.getByText(/^Version 2 of 2/)).toBeVisible()
  await expect(page.getByText(/need review after a document was replaced/)).toHaveCount(0)
})
