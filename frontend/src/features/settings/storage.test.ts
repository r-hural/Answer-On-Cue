import { describe, expect, it } from 'vitest'
import { storageBackend } from './storage.mocks'
import {
  applyRestore,
  audioPreview,
  backupUrl,
  countsSummary,
  deleteRecordings,
  discardRestore,
  exportUrl,
  fetchStorage,
  formatBytes,
  makeBackup,
  makeExport,
  retentionPreview,
  saveRetention,
  uploadRestore,
} from './storage'
import { counts } from './storage.mocks'

describe('storage client', () => {
  it('reads the storage details', async () => {
    const world = storageBackend()
    expect((await fetchStorage()).audio_retention).toBe('forever')
    expect(world.requests).toEqual([])
  })

  it('makes a backup and an export with POST', async () => {
    const world = storageBackend()
    expect((await makeBackup()).name).toMatch(/^answeroncue-backup-/)
    expect((await makeExport()).name).toMatch(/^answeroncue-history-/)
    expect(world.requests.map((r) => [r.method, r.path])).toEqual([
      ['POST', '/api/data/backups'],
      ['POST', '/api/data/exports'],
    ])
  })

  it('downloads are plain links', () => {
    expect(backupUrl('answeroncue-backup-2026-10-22-091500.zip')).toBe(
      '/api/data/backups/answeroncue-backup-2026-10-22-091500.zip',
    )
    expect(exportUrl('answeroncue-history-2026-10-22-091500.zip')).toBe(
      '/api/data/exports/answeroncue-history-2026-10-22-091500.zip',
    )
  })

  it('uploads the backup as one multipart file, then applies or discards it by token', async () => {
    const world = storageBackend()
    const file = new File(['zip'], 'answeroncue-backup-2026-10-15.zip', { type: 'application/zip' })
    const preview = await uploadRestore(file)
    await applyRestore(preview.token)
    await discardRestore(preview.token)
    expect(world.requests.map((r) => [r.method, r.path])).toEqual([
      ['POST', '/api/data/restore'],
      ['POST', `/api/data/restore/${preview.token}/apply`],
      ['DELETE', `/api/data/restore/${preview.token}`],
    ])
    // jsdom hands the file over as an unnamed blob; a browser keeps its name
    expect(world.requests[0].body).toEqual({ file: expect.any(String) })
  })

  it('previews and saves the retention policy with the fingerprint', async () => {
    const world = storageBackend()
    const shown = await retentionPreview('30d')
    await saveRetention('30d', shown.fingerprint)
    expect(world.requests).toEqual([
      { method: 'GET', path: '/api/data/retention/preview?policy=30d', body: undefined },
      {
        method: 'PUT',
        path: '/api/data/retention',
        body: { policy: '30d', fingerprint: shown.fingerprint },
      },
    ])
  })

  it('previews and deletes recordings by scope', async () => {
    const world = storageBackend()
    await audioPreview({ scope: 'older_than', days: 30 })
    await audioPreview({ scope: 'all' })
    await audioPreview({ scope: 'subject', subjectId: 7 })
    await deleteRecordings({ scope: 'subject', subjectId: 7 }, 'fp')
    await deleteRecordings({ scope: 'older_than', days: 30 }, 'fp2')
    expect(world.requests.map((r) => [r.method, r.path])).toEqual([
      ['GET', '/api/data/recordings/deletion-preview?scope=older_than&days=30'],
      ['GET', '/api/data/recordings/deletion-preview?scope=all'],
      ['GET', '/api/data/recordings/deletion-preview?scope=subject&subject_id=7'],
      ['POST', '/api/data/recordings/delete'],
      ['POST', '/api/data/recordings/delete'],
    ])
    expect(world.requests[3].body).toEqual({ scope: 'subject', subject_id: 7, fingerprint: 'fp' })
    expect(world.requests[4].body).toEqual({ scope: 'older_than', days: 30, fingerprint: 'fp2' })
  })
})

describe('formatting', () => {
  it('formats sizes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(212 * 1024)).toBe('212 KB')
    expect(formatBytes(2.1 * 1024 ** 2)).toBe('2.1 MB')
    expect(formatBytes(1.4 * 1024 ** 3)).toBe('1.4 GB')
    expect(formatBytes(1024 ** 3)).toBe('1 GB')
    expect(formatBytes(212 * 1024 ** 2)).toBe('212 MB')
    expect(formatBytes(0.8 * 1024 ** 3)).toBe('0.8 GB')
  })

  it('summarises counts with singular forms', () => {
    expect(countsSummary(counts)).toBe('2 subjects, 5 documents, 127 questions, 6 rounds, 49 recordings')
    expect(countsSummary({ ...counts, subjects: 1, rounds: 1 })).toContain('1 subject, 5')
    expect(countsSummary({ ...counts, subjects: 1, rounds: 1 })).toContain('1 round, 49')
  })
})
