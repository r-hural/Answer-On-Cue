import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const python = path.resolve(import.meta.dirname, '../../backend/.venv/Scripts/python.exe')
const backend = path.resolve(import.meta.dirname, '../../backend')
const seed = path.resolve(import.meta.dirname, 'seed_backup.py')
const dataDir = path.resolve(import.meta.dirname, '../test-results/data')

// The e2e database is shared with the other specs, so this one never applies a restore, never
// changes the retention policy and deletes only the recordings of its own seeded subject.
test.describe('storage and backup', () => {
  let name: string
  let subjectId: number

  test.beforeAll(() => {
    name = `E2E backup ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`
    const out = execFileSync(python, [seed, name], {
      cwd: backend,
      env: { ...process.env, AOC_DATA_DIR: dataDir },
      encoding: 'utf-8',
    })
    const ids = JSON.parse(out.trim().split('\n').pop()!) as { subject_id: number }
    subjectId = ids.subject_id
  })

  test('backup, restore preview, export, recordings of one subject, subject deletion', async ({ page }) => {
    await page.goto('/settings#storage')
    const section = page.locator('section#storage')
    await expect(section.getByRole('heading', { level: 2, name: 'Storage and backup' })).toBeVisible()

    // 1. A backup, downloaded from the ready notice.
    await section.getByRole('button', { name: 'Make a backup', exact: true }).click()
    const ready = section.getByRole('status').filter({ hasText: 'Backup ready' })
    await expect(ready).toBeVisible()
    const [backup] = await Promise.all([
      page.waitForEvent('download'),
      ready.getByRole('link', { name: /^answeroncue-backup-/ }).click(),
    ])
    expect(backup.suggestedFilename()).toMatch(/^answeroncue-backup-\d{4}-\d{2}-\d{2}-\d{6}\.zip$/)
    const backupFile = await backup.path()

    // 2. Restore: only the preview, then Cancel. A restore is never applied here.
    await section.getByRole('button', { name: 'Restore from a backup…', exact: true }).click()
    const restore = page.getByRole('dialog', { name: 'Restore from a backup?' })
    await restore.getByLabel('Backup file').setInputFiles(backupFile)
    await expect(restore.getByText(/^Backup from /)).toBeVisible()
    await expect(restore.getByText(/\d+ subjects?/)).toBeVisible()
    await restore.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(restore).toBeHidden()

    // 3. The export.
    await section.getByRole('button', { name: 'Export history and transcripts', exact: true }).click()
    const exported = section.getByRole('status').filter({ hasText: 'Export ready' })
    await expect(exported).toBeVisible()
    const [history] = await Promise.all([
      page.waitForEvent('download'),
      exported.getByRole('link', { name: /^answeroncue-history-/ }).click(),
    ])
    expect(history.suggestedFilename()).toMatch(/^answeroncue-history-/)

    // 4. Recordings: the policy is only read; only the seeded subject's recording is deleted.
    await expect(section.getByLabel('Keep recordings')).toHaveValue('forever')
    await section.getByRole('button', { name: 'Delete recordings now…', exact: true }).click()
    const audio = page.getByRole('dialog', { name: 'Delete recordings' })
    await audio.getByRole('radio', { name: /One subject/ }).check()
    await audio.getByLabel('Subject', { exact: true }).selectOption({ label: name })
    const remove = audio.getByRole('button', { name: 'Delete 1 recording', exact: true })
    await expect(remove).toBeVisible()
    await remove.click()
    await expect(section.getByRole('status').filter({ hasText: 'Deleted 1 recording' })).toBeVisible()

    // 5. The subject's deletion shows exact counts, and the recording is already gone.
    await page.goto(`/library/${subjectId}`)
    await page.getByRole('button', { name: 'Delete subject', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('row', { name: /^Rounds and answers/ })).toContainText('1 round, 1 answer')
    await expect(dialog.getByRole('row', { name: /^Recordings/ })).toContainText('0 (0 B)')
    await dialog.getByLabel(/^Type the subject name/).fill(name)
    await dialog.getByRole('button', { name: 'Delete permanently', exact: true }).click()
    await expect(page).toHaveURL(/\/library$/)
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  })
})
