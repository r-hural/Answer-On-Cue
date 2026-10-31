import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { Notice } from '../../ui/Notice'
import { AudioDeleteDialog } from './AudioDeleteDialog'
import { CleanupFields } from './CleanupFields'
import { RestoreDialog } from './RestoreDialog'
import { settingsStrings } from './strings'
import { useEscape } from './useEscape'
import {
  type AudioPreview,
  type AudioResult,
  type RetentionPolicy,
  type StorageInfo,
  backupUrl,
  changedPreview,
  exportUrl,
  fetchStorage,
  formatBytes,
  makeBackup,
  makeExport,
  recordingsOf,
  retentionPreview,
  saveRetention,
} from './storage'

const s = settingsStrings.storage

const POLICIES: RetentionPolicy[] = ['forever', 'assessed', '30d', '90d']

type Ready = { kind: 'backup' | 'export'; name: string; bytes: number }
type Dialog =
  | { kind: 'restore' }
  | { kind: 'audio' }
  | { kind: 'retention'; policy: RetentionPolicy; preview: AudioPreview; error: string | null }

const messageOf = (failure: unknown) => (failure as Error).message

export function deletedText(result: AudioResult): string {
  const text = s.deleted(recordingsOf(result.deleted), formatBytes(result.bytes))
  return result.skipped_in_use > 0 ? `${text} ${s.skipped(result.skipped_in_use)}` : text
}

/** Where a finished restore goes: the home page, reloaded so no old data stays on screen. */
const goHome = () => window.location.assign('/')

export function StorageSection({ onRestored = goHome }: { onRestored?: () => void }) {
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [ready, setReady] = useState<Ready | null>(null)
  const [busy, setBusy] = useState<'backup' | 'export' | 'retention' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)

  const refresh = useCallback(async () => {
    try {
      setInfo(await fetchStorage())
      setLoadError(false)
    } catch {
      setLoadError(true)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchStorage()
      .then((loaded) => !cancelled && setInfo(loaded))
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
    }
  }, [])

  async function run(kind: 'backup' | 'export') {
    setBusy(kind)
    setError(null)
    setDone(null)
    try {
      const made = kind === 'backup' ? await makeBackup() : await makeExport()
      setReady({ kind, name: made.name, bytes: made.bytes })
      void refresh()
    } catch (failure) {
      setReady(null)
      setError(messageOf(failure))
    } finally {
      setBusy(null)
    }
  }

  async function changeRetention(policy: RetentionPolicy) {
    setError(null)
    setDone(null)
    setBusy('retention')
    try {
      const preview = await retentionPreview(policy)
      if (preview.recordings === 0) {
        await saveRetention(policy, preview.fingerprint)
        setDone(s.retentionSaved)
        await refresh()
      } else {
        setDialog({ kind: 'retention', policy, preview, error: null })
      }
    } catch (failure) {
      setError(messageOf(failure))
    } finally {
      setBusy(null)
    }
  }

  async function confirmRetention(dialogState: Extract<Dialog, { kind: 'retention' }>) {
    try {
      const result = await saveRetention(dialogState.policy, dialogState.preview.fingerprint)
      setDialog(null)
      setDone(`${s.retentionSaved} ${deletedText(result)}`)
      await refresh()
    } catch (failure) {
      const shown = failure instanceof ApiError ? changedPreview(failure.detail) : null
      setDialog({
        ...dialogState,
        preview: shown ?? dialogState.preview,
        error: messageOf(failure),
      })
    }
  }

  const head = <h2 id="h-storage">{s.title}</h2>
  if (loadError)
    return (
      <section className="set" id="storage" aria-labelledby="h-storage">
        {head}
        <Notice kind="error">{s.loadError}</Notice>
      </section>
    )
  if (!info)
    return (
      <section className="set" id="storage" aria-labelledby="h-storage">
        {head}
        <p className="muted">{settingsStrings.loading}</p>
      </section>
    )

  return (
    <section className="set" id="storage" aria-labelledby="h-storage">
      {head}
      <p className="lead">
        {s.folder} <code>{info.data_dir}</code> ·{' '}
        <span className="num">{s.size(formatBytes(info.total_bytes), formatBytes(info.recordings_bytes))}</span>
      </p>
      {ready && (
        <Notice kind="ok" className="mb-6">
          <strong>{ready.kind === 'backup' ? s.backupReady : s.exportReady}</strong>
          <a
            className="file-name"
            href={ready.kind === 'backup' ? backupUrl(ready.name) : exportUrl(ready.name)}
            download={ready.name}
          >
            {ready.name}
          </a>{' '}
          <span className="num">({formatBytes(ready.bytes)})</span>{' '}
          {ready.kind === 'backup' ? `${s.backupText} ${s.backupKeys}` : s.exportText}
        </Notice>
      )}
      {done && (
        <Notice kind="ok" className="mb-6">
          {done}
        </Notice>
      )}
      {error && (
        <Notice kind="error" className="mb-6">
          {error}
        </Notice>
      )}
      <div className="cols-2">
        <div>
          <h3>{s.backupHeading}</h3>
          <p className="small muted">{s.backupHelp}</p>
          <div className="actions mt-3">
            <button className="btn" type="button" disabled={busy !== null} onClick={() => run('backup')}>
              {busy === 'backup' ? s.working : s.makeBackup}
            </button>
            <button className="btn" type="button" onClick={() => setDialog({ kind: 'restore' })}>
              {s.restore}
            </button>
          </div>
          <h3>{s.exportHeading}</h3>
          <p className="small muted">{s.exportHelp}</p>
          <div className="actions mt-3">
            <button className="btn" type="button" disabled={busy !== null} onClick={() => run('export')}>
              {busy === 'export' ? s.working : s.exportButton}
            </button>
          </div>
          <CleanupFields
            key={`${info.pre_restore_keep}/${info.export_keep_days}`}
            info={info}
            onSaved={(message) => {
              setReady(null)
              setError(null)
              setDone(message)
              void refresh()
            }}
            onError={(message) => {
              setDone(null)
              setError(message)
            }}
          />
        </div>
        <div>
          <h3>{s.recordings}</h3>
          <div className="field">
            <label className="label" htmlFor="ret">
              {s.keep}
            </label>
            <select
              className="select"
              id="ret"
              value={info.audio_retention}
              disabled={busy === 'retention'}
              onChange={(event) => changeRetention(event.target.value as RetentionPolicy)}
            >
              {POLICIES.map((policy) => (
                <option key={policy} value={policy}>
                  {s.policies[policy]}
                </option>
              ))}
            </select>
            <span className="hint">{s.keepHint}</span>
          </div>
          <div className="actions mt-4">
            <button className="btn danger" type="button" onClick={() => setDialog({ kind: 'audio' })}>
              {s.deleteNow}
            </button>
          </div>
        </div>
      </div>
      {dialog?.kind === 'restore' && (
        <RestoreDialog onClose={() => setDialog(null)} onRestored={onRestored} />
      )}
      {dialog?.kind === 'audio' && (
        <AudioDeleteDialog
          onClose={() => setDialog(null)}
          onDone={(result) => {
            setDialog(null)
            setDone(deletedText(result))
            void refresh()
          }}
        />
      )}
      {dialog?.kind === 'retention' && (
        <RetentionDialog
          state={dialog}
          onCancel={() => setDialog(null)}
          onConfirm={() => confirmRetention(dialog)}
        />
      )}
    </section>
  )
}

function RetentionDialog({
  state,
  onCancel,
  onConfirm,
}: {
  state: Extract<Dialog, { kind: 'retention' }>
  onCancel: () => void
  onConfirm: () => void
}) {
  const { preview } = state
  useEscape(onCancel)
  return (
    <div className="scrim">
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="retention-title">
        <h2 id="retention-title">{s.retentionTitle(s.policies[state.policy])}</h2>
        {state.error && <Notice kind="warn">{state.error}</Notice>}
        <p>
          <strong>{s.willDelete(recordingsOf(preview.recordings), formatBytes(preview.bytes))}</strong>
        </p>
        <p>{s.stays}</p>
        {preview.kept_unresolved > 0 && (
          <p className="small muted">{s.keptUnresolved(recordingsOf(preview.kept_unresolved))}</p>
        )}
        <div className="actions">
          <button className="btn" type="button" autoFocus onClick={onCancel}>
            {s.cancel}
          </button>
          <button
            className="btn danger solid"
            type="button"
            disabled={preview.recordings === 0}
            onClick={onConfirm}
          >
            {s.deleteButton(recordingsOf(preview.recordings))}
          </button>
        </div>
      </section>
    </div>
  )
}
