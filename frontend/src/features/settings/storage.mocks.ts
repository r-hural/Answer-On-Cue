import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import type {
  AudioPreview,
  AudioResult,
  BackupInfo,
  CleanupResult,
  Counts,
  ExportInfo,
  RestorePreview,
  RetentionPolicy,
  StorageInfo,
} from './storage'

export const counts: Counts = {
  subjects: 2,
  documents: 5,
  document_versions: 6,
  topics: 14,
  questions: 127,
  rounds: 6,
  answers: 40,
  recordings: 49,
}

export const storageInfo: StorageInfo = {
  data_dir: 'C:\\Users\\user\\AppData\\Local\\AnswerOnCue\\data',
  total_bytes: 1.4 * 1024 ** 3,
  database_bytes: 12 * 1024 ** 2,
  documents_bytes: 3 * 1024 ** 2,
  recordings_bytes: 1.1 * 1024 ** 3,
  recordings: 58,
  recordings_deleted: 0,
  audio_retention: 'forever',
  pre_restore_keep: 5,
  export_keep_days: 30,
}

export const backupInfo: BackupInfo = {
  name: 'answeroncue-backup-2026-10-22-091500.zip',
  bytes: 1.3 * 1024 ** 3,
  created_at: '2026-10-22T07:15:00Z',
  counts,
}

export const exportInfo: ExportInfo = {
  name: 'answeroncue-history-2026-10-22-091500.zip',
  bytes: 48 * 1024,
  rounds: 6,
  answers: 40,
}

export const restorePreview: RestorePreview = {
  token: 'a'.repeat(32),
  created_at: '2026-10-15T08:00:00Z',
  app_version: '0.9.0',
  counts,
  current: { ...counts, rounds: 7 },
  newer_rounds: 1,
}

export function preview(recordings: number, bytes: number, patch: Partial<AudioPreview> = {}) {
  return { recordings, bytes, kept_unresolved: 0, fingerprint: `fp-${recordings}-${bytes}`, ...patch }
}

export interface Seen {
  method: string
  path: string
  body: unknown
}

export interface StorageWorld {
  requests: Seen[]
  info: StorageInfo
  /** What each retention policy would delete now. */
  retention: Record<RetentionPolicy, AudioPreview>
  older: AudioPreview
  all: AudioPreview
  subject: AudioPreview
  /** The next delete / retention save answers 409 with this selection. */
  changed: AudioPreview | null
  restore: RestorePreview
  uploadError: string | null
  applyError: string | null
  result: AudioResult
  /** What the next clean-up save deleted. */
  cleanup: Pick<CleanupResult, 'deleted_backups' | 'deleted_exports'>
  /** The next clean-up save answers 422 with this detail. */
  cleanupError: string | null
}

export function storageWorld(patch: Partial<StorageWorld> = {}): StorageWorld {
  return {
    requests: [],
    info: { ...storageInfo },
    retention: {
      forever: preview(0, 0),
      assessed: preview(30, 500_000_000),
      '30d': preview(41, 0.8 * 1024 ** 3),
      '90d': preview(12, 200_000_000),
    },
    older: preview(41, 0.8 * 1024 ** 3),
    all: preview(58, 1.1 * 1024 ** 3),
    subject: preview(18, 2.1 * 1024 ** 2),
    changed: null,
    restore: restorePreview,
    uploadError: null,
    applyError: null,
    result: { deleted: 41, bytes: 0.8 * 1024 ** 3, skipped_in_use: 0 },
    cleanup: { deleted_backups: 0, deleted_exports: 0 },
    cleanupError: null,
    ...patch,
  }
}

export function storageGet(world: StorageWorld = storageWorld()) {
  return http.get('/api/data/storage', () => HttpResponse.json(world.info))
}

/** Handlers for every /api/data endpoint, recording the changes in `world.requests`. */
export function storageHandlers(world: StorageWorld) {
  const log = async (request: Request, body?: unknown) => {
    const url = new URL(request.url)
    world.requests.push({
      method: request.method,
      path: url.pathname + url.search,
      body: body ?? (request.method === 'GET' ? undefined : await request.clone().json().catch(() => undefined)),
    })
  }
  const pick = (url: URL): AudioPreview => {
    const scope = url.searchParams.get('scope')
    return scope === 'all' ? world.all : scope === 'subject' ? world.subject : world.older
  }
  const conflict = (message: string) => {
    const shown = world.changed!
    world.changed = null
    return HttpResponse.json({ detail: { message, preview: shown } }, { status: 409 })
  }
  return [
    storageGet(world),
    http.post('/api/data/backups', async ({ request }) => {
      await log(request)
      return HttpResponse.json(backupInfo, { status: 201 })
    }),
    http.post('/api/data/exports', async ({ request }) => {
      await log(request)
      return HttpResponse.json(exportInfo, { status: 201 })
    }),
    http.post('/api/data/restore', async ({ request }) => {
      // jsdom's File is not undici's, so the multipart body is read as text.
      const raw = await request.clone().text()
      await log(request, { file: /filename="([^"]+)"/.exec(raw)?.[1] })
      if (world.uploadError)
        return HttpResponse.json({ detail: world.uploadError }, { status: 422 })
      return HttpResponse.json(world.restore)
    }),
    http.post('/api/data/restore/:token/apply', async ({ request }) => {
      await log(request)
      if (world.applyError) return HttpResponse.json({ detail: world.applyError }, { status: 409 })
      return HttpResponse.json({ restored: counts, pre_restore_backup: 'answeroncue-pre-restore-x.zip' })
    }),
    http.delete('/api/data/restore/:token', async ({ request }) => {
      await log(request)
      return new HttpResponse(null, { status: 204 })
    }),
    http.get('/api/data/retention/preview', async ({ request }) => {
      const policy = new URL(request.url).searchParams.get('policy') as RetentionPolicy
      await log(request)
      return HttpResponse.json(world.retention[policy])
    }),
    http.put('/api/data/retention', async ({ request }) => {
      const body = (await request.clone().json()) as { policy: RetentionPolicy }
      await log(request, body)
      if (world.changed) return conflict('The recordings changed since you looked.')
      world.info = { ...world.info, audio_retention: body.policy }
      return HttpResponse.json(world.result)
    }),
    http.put('/api/data/cleanup', async ({ request }) => {
      const body = (await request.clone().json()) as Pick<CleanupResult, 'pre_restore_keep' | 'export_keep_days'>
      await log(request, body)
      if (world.cleanupError) return HttpResponse.json({ detail: world.cleanupError }, { status: 422 })
      world.info = { ...world.info, ...body }
      return HttpResponse.json({ ...body, ...world.cleanup })
    }),
    http.get('/api/data/recordings/deletion-preview', async ({ request }) => {
      await log(request)
      return HttpResponse.json(pick(new URL(request.url)))
    }),
    http.post('/api/data/recordings/delete', async ({ request }) => {
      await log(request)
      if (world.changed) return conflict('The recordings changed since you looked.')
      return HttpResponse.json(world.result)
    }),
  ]
}

/** Installs the handlers and returns the world to inspect. */
export function storageBackend(patch: Partial<StorageWorld> = {}): StorageWorld {
  const world = storageWorld(patch)
  server.use(...storageHandlers(world))
  return world
}

export function changesOf(world: StorageWorld): Seen[] {
  return world.requests.filter((seen) => seen.method !== 'GET')
}
