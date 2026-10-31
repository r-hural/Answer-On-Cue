import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

/**
 * Chapters against the real backend (no stubs): seed_chapters.py adds a subject with two books in
 * its default chapter; book B moves to a new chapter through the UI, and Practice counts per chapter.
 * Seed: "Demokracja ateńska" (book A) has 3 materials + 1 expanded + 1 outside question; "Stany
 * Generalne" (book B) has 3 materials questions; "Demokracja" draws on both books and stays.
 */

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_chapters.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

test('a book moves to its own chapter; topics and Practice follow the chapters', async ({ page }) => {
  const name = `E2E chapters int ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const out = execFileSync(python, [seed, name], {
    cwd: backend,
    env: { ...process.env, AOC_DATA_DIR: dataDir },
    encoding: 'utf-8',
  })
  const { subject_id: subjectId } = JSON.parse(out.trim().split('\n').pop()!) as {
    subject_id: number
    documents: { A: number; B: number }
  }

  // the subject page: one chapter, no tabs; book B moves to a new chapter
  await page.goto(`/library/${subjectId}`)
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  const move = page.getByRole('button', { name: 'Move Rewolucja francuska', exact: true })
  await expect(move).toBeEnabled()
  await expect(page.getByRole('navigation', { name: 'Chapters' })).toHaveCount(0)
  await move.click()
  const dialog = page.getByRole('dialog', { name: 'Move to a chapter' })
  await expect(dialog.getByRole('radio', { name: 'New chapter', exact: true })).toBeChecked()
  await dialog.getByLabel('Name of the new chapter').fill('Rewolucja francuska')
  const submit = dialog.getByRole('button', { name: 'Move', exact: true })
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(page.getByText('Moved, with 1 topic.', { exact: true })).toBeVisible()
  await expect(dialog).toHaveCount(0)

  // chapter tabs; each chapter shows its own topics
  const tabs = page.getByRole('navigation', { name: 'Chapters' })
  await expect(tabs.getByRole('link')).toHaveText(['Unnamed chapter', 'Rewolucja francuska'])
  const topicsSection = page.locator('#topics')
  const topics = page.locator('#topics-list')
  await tabs.getByRole('link', { name: 'Rewolucja francuska', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Move Rewolucja francuska', exact: true })).toBeVisible()
  await expect(topicsSection.getByText('1 topic', { exact: true })).toBeVisible()
  const show = page.getByRole('button', { name: 'Show topics', exact: true })
  await expect(show).toBeEnabled()
  await show.click()
  await expect(topics.getByText('Stany Generalne', { exact: true })).toBeVisible()
  await expect(topics.getByText('Demokracja ateńska', { exact: true })).toHaveCount(0)

  await tabs.getByRole('link', { name: 'Unnamed chapter', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Move Starożytna Grecja', exact: true })).toBeVisible()
  await expect(topicsSection.getByText('2 topics', { exact: true })).toBeVisible()
  await expect(topics.getByText('Demokracja ateńska', { exact: true })).toBeVisible()
  await expect(topics.getByText('Demokracja', { exact: true })).toBeVisible()
  await expect(topics.getByText('Stany Generalne', { exact: true })).toHaveCount(0)

  // Practice: the B chapter, whole chapter, My materials
  await page.goto(`/practice?subject=${subjectId}`)
  const chapter = page.getByLabel('Chapter', { exact: true })
  await expect(chapter).toBeEnabled()
  await expect(chapter.getByRole('option')).toHaveText(['Unnamed chapter', 'Rewolucja francuska'])
  await chapter.selectOption({ label: 'Rewolucja francuska' })
  const scope = page.getByRole('radiogroup', { name: 'Scope' })
  const whole = scope.getByRole('radio', { name: 'Whole chapter', exact: true })
  await whole.check()
  const sources = page.getByRole('radiogroup', { name: 'Questions' })
  await sources.getByRole('radio', { name: /^My materials/ }).check()
  await expect(page.getByText('(3 available)', { exact: true })).toBeVisible()
  // the B chapter has materials questions only, so All questions gives the same 3
  const all = sources.getByRole('radio', { name: /^All questions/ })
  await all.check()
  await expect(page.getByText('(3 available)', { exact: true })).toBeVisible()

  // the default chapter under All questions: 3 materials + 1 expanded + 1 outside
  await chapter.selectOption({ label: 'Unnamed chapter' })
  await expect(whole).toBeChecked()
  await expect(all).toBeChecked()
  await expect(page.getByText('(5 available)', { exact: true })).toBeVisible()
})
