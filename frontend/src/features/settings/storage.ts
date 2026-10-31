import { apiGet, apiSend, apiUpload } from '../../api/client'

export type RetentionPolicy = 'forever' | 'assessed' | '30d' | '90d'
export type AudioScope = 'older_than' | 'all' | 'subject'

export interface StorageInfo {
  data_dir: string
  total_bytes: number
  database_bytes: number
  documents_bytes: number
  recordings_bytes: number
  recordings: number
  recordings_deleted: number
  audio_retention: RetentionPolicy
  /** The newest pre-restore backups kept; 0 keeps all. */
  pre_restore_keep: number
  /** Exports are deleted after this many days; 0 keeps all. */
  export_keep_days: number
}

export interface CleanupResult {
  pre_restore_keep: number
  export_keep_days: number
  deleted_backups: number
  deleted_exports: number
}

export interface Counts {
  subjects: number
  documents: number
  document_versions: number
  topics: number
  questions: number
  rounds: number
  answers: number
  recordings: number
}

export interface BackupInfo {
  name: string
  bytes: number
  created_at: string
  counts: Counts
}

export interface ExportInfo {
  name: string
  bytes: number
  rounds: number
  answers: number
}

export interface RestorePreview {
  token: string
  created_at: string
  app_version: string
  counts: Counts
  current: Counts
  newer_rounds: number
}

export interface RestoreResult {
  restored: Counts
  pre_restore_backup: string
}

export interface AudioPreview {
  recordings: number
  bytes: number
  kept_unresolved: number
  fingerprint: string
}

export interface AudioResult {
  deleted: number
  bytes: number
  skipped_in_use: number
}

export interface AudioChoice {
  scope: AudioScope
  days?: number
  subjectId?: number
}

export const OLDER_THAN_DAYS = 30

export const fetchStorage = () => apiGet<StorageInfo>('/data/storage')

export const makeBackup = () => apiSend<BackupInfo>('POST', '/data/backups')
export const backupUrl = (name: string) => `/api/data/backups/${encodeURIComponent(name)}`
export const makeExport = () => apiSend<ExportInfo>('POST', '/data/exports')
export const exportUrl = (name: string) => `/api/data/exports/${encodeURIComponent(name)}`

export const uploadRestore = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiUpload<RestorePreview>('/data/restore', form)
}
export const applyRestore = (token: string) =>
  apiSend<RestoreResult>('POST', `/data/restore/${token}/apply`)
export const discardRestore = (token: string) => apiSend<void>('DELETE', `/data/restore/${token}`)

export const retentionPreview = (policy: RetentionPolicy) =>
  apiGet<AudioPreview>(`/data/retention/preview?policy=${policy}`)
export const saveRetention = (policy: RetentionPolicy, fingerprint: string) =>
  apiSend<AudioResult>('PUT', '/data/retention', { policy, fingerprint })

export const saveCleanup = (preRestoreKeep: number, exportKeepDays: number) =>
  apiSend<CleanupResult>('PUT', '/data/cleanup', {
    pre_restore_keep: preRestoreKeep,
    export_keep_days: exportKeepDays,
  })

function choiceQuery(choice: AudioChoice): string {
  const query = new URLSearchParams({ scope: choice.scope })
  if (choice.days !== undefined) query.set('days', String(choice.days))
  if (choice.subjectId !== undefined) query.set('subject_id', String(choice.subjectId))
  return query.toString()
}

export const audioPreview = (choice: AudioChoice) =>
  apiGet<AudioPreview>(`/data/recordings/deletion-preview?${choiceQuery(choice)}`)
export const deleteRecordings = (choice: AudioChoice, fingerprint: string) =>
  apiSend<AudioResult>('POST', '/data/recordings/delete', {
    scope: choice.scope,
    days: choice.days,
    subject_id: choice.subjectId,
    fingerprint,
  })

/** The preview a 409 answer carries (the selection changed since the numbers were shown). */
export function changedPreview(detail: unknown): AudioPreview | null {
  if (detail && typeof detail === 'object' && 'preview' in detail) {
    return (detail as { preview: AudioPreview }).preview
  }
  return null
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

/** 0 B, 212 KB, 2.1 MB, 0.8 GB, 1.4 GB: the next unit from 800, one decimal below 100. */
export function formatBytes(bytes: number): string {
  let value = bytes
  let unit = 0
  while (value >= 800 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  if (unit === 0) return `${Math.round(value)} B`
  const text = value >= 100 ? value.toFixed(0) : value.toFixed(1).replace(/\.0$/, '')
  return `${text} ${UNITS[unit]}`
}

const noun = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`

export const recordingsOf = (count: number) => noun(count, 'recording')
export const roundsOf = (count: number) => noun(count, 'round')

/** "2 subjects, 5 documents, 127 questions, 6 rounds, 49 recordings". */
export function countsSummary(counts: Counts): string {
  return [
    noun(counts.subjects, 'subject'),
    noun(counts.documents, 'document'),
    noun(counts.questions, 'question'),
    noun(counts.rounds, 'round'),
    noun(counts.recordings, 'recording'),
  ].join(', ')
}

/** "15 Oct 2026". */
export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}
