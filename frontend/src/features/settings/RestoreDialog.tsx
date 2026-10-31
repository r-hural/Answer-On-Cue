import { useEffect, useRef, useState } from 'react'
import { Notice } from '../../ui/Notice'
import { settingsStrings } from './strings'
import { useEscape } from './useEscape'
import {
  type RestorePreview,
  applyRestore,
  countsSummary,
  discardRestore,
  roundsOf,
  shortDate,
  uploadRestore,
} from './storage'

const s = settingsStrings.storage

type Step =
  | { kind: 'choose' }
  | { kind: 'checking'; file: string }
  | { kind: 'ready'; file: string; preview: RestorePreview }
  | { kind: 'restoring'; file: string; preview: RestorePreview }

/** Picks a backup file, shows what it holds and what it replaces, and applies it on request. */
export function RestoreDialog({
  onClose,
  onRestored,
}: {
  onClose: () => void
  onRestored: () => void
}) {
  const [step, setStep] = useState<Step>({ kind: 'choose' })
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  // Set once the dialog is cancelled or gone, so an upload still in flight is discarded on return.
  const closed = useRef(false)
  useEffect(() => {
    closed.current = false
    return () => {
      closed.current = true
    }
  }, [])

  async function choose(file: File | undefined) {
    if (!file) return
    setError(null)
    setStep({ kind: 'checking', file: file.name })
    try {
      const preview = await uploadRestore(file)
      if (closed.current) {
        // Cancelled while the file was being checked: its staged copy is not needed any more.
        await release(preview.token)
        return
      }
      setStep({ kind: 'ready', file: file.name, preview })
    } catch (failure) {
      if (closed.current) return
      setError((failure as Error).message)
      setStep({ kind: 'choose' })
    }
  }

  async function release(token: string) {
    // The staged copy is only a convenience; failing to remove it must not block the user.
    await discardRestore(token).catch(() => undefined)
  }

  async function cancel() {
    closed.current = true
    if (step.kind === 'ready') await release(step.preview.token)
    onClose()
  }

  async function another() {
    if (step.kind !== 'ready') return
    await release(step.preview.token)
    setStep({ kind: 'choose' })
    setError(null)
  }

  async function apply() {
    if (step.kind !== 'ready') return
    setError(null)
    setStep({ kind: 'restoring', file: step.file, preview: step.preview })
    try {
      await applyRestore(step.preview.token)
      onRestored()
    } catch (failure) {
      setError((failure as Error).message)
      setStep({ kind: 'ready', file: step.file, preview: step.preview })
    }
  }

  const shown = step.kind === 'ready' || step.kind === 'restoring' ? step : null
  useEscape(() => {
    if (step.kind !== 'restoring') void cancel()
  })
  return (
    <div className="scrim">
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="restore-title">
        <h2 id="restore-title">{s.restoreTitle}</h2>
        {error && <Notice kind="error">{error}</Notice>}
        {step.kind === 'choose' && (
          <div className="field">
            <label className="label" htmlFor="restore-file">
              {s.backupFile}
            </label>
            <input
              ref={input}
              className="input"
              id="restore-file"
              type="file"
              accept=".zip,application/zip"
              autoFocus
              onChange={(event) => choose(event.target.files?.[0])}
            />
          </div>
        )}
        {step.kind === 'checking' && <p className="muted">{s.checking}</p>}
        {shown && (
          <>
            <div className="field">
              <span className="label">{s.backupFile}</span>
              <div className="actions">
                <span className="file-name">{shown.file}</span>
                <button
                  className="btn small"
                  type="button"
                  disabled={step.kind === 'restoring'}
                  onClick={another}
                >
                  {s.chooseAnother}
                </button>
              </div>
            </div>
            <p>
              <span className="num">
                {s.fromBackup(shortDate(shown.preview.created_at), countsSummary(shown.preview.counts))}
              </span>
            </p>
            <Notice kind="warn">
              <strong>{s.replaceTitle}</strong>
              {shown.preview.newer_rounds > 0 &&
                s.newerRounds(roundsOf(shown.preview.newer_rounds), shortDate(shown.preview.created_at))}
              {s.keysStay}
            </Notice>
            <p className="small muted">{s.restoreTip}</p>
          </>
        )}
        <div className="actions">
          <button className="btn" type="button" disabled={step.kind === 'restoring'} onClick={cancel}>
            {s.cancel}
          </button>
          {shown && (
            <button
              className="btn danger solid"
              type="button"
              disabled={step.kind === 'restoring'}
              onClick={apply}
            >
              {step.kind === 'restoring' ? s.restoring : s.restoreConfirm}
            </button>
          )}
        </div>
      </section>
    </div>
  )
}
