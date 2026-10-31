import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { Notice } from '../../ui/Notice'
import { type Subject, listSubjects } from '../library/api'
import { settingsStrings } from './strings'
import { useEscape } from './useEscape'
import {
  type AudioChoice,
  type AudioPreview,
  type AudioResult,
  type AudioScope,
  OLDER_THAN_DAYS,
  audioPreview,
  changedPreview,
  deleteRecordings,
  formatBytes,
  recordingsOf,
} from './storage'

const s = settingsStrings.storage

type Previews = Partial<Record<'older_than' | 'all' | 'subject', AudioPreview>>

/** Deletes recordings by scope, always after showing the exact counts. */
export function AudioDeleteDialog({
  onClose,
  onDone,
}: {
  onClose: () => void
  onDone: (result: AudioResult) => void
}) {
  const [scope, setScope] = useState<AudioScope>('older_than')
  const [subjects, setSubjects] = useState<Subject[]>([])
  const [subjectId, setSubjectId] = useState<number | null>(null)
  const [previews, setPreviews] = useState<Previews>({})
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const choiceOf = (which: AudioScope, subject: number | null): AudioChoice =>
    which === 'older_than'
      ? { scope: which, days: OLDER_THAN_DAYS }
      : which === 'subject'
        ? { scope: which, subjectId: subject ?? undefined }
        : { scope: which }

  // The numbers of the first two choices and the subjects are read once, when the dialog opens.
  useEffect(() => {
    let cancelled = false
    Promise.all([
      audioPreview(choiceOf('older_than', null)),
      audioPreview(choiceOf('all', null)),
      listSubjects(),
    ])
      .then(([older, all, found]) => {
        if (cancelled) return
        setPreviews((current) => ({ ...current, older_than: older, all }))
        setSubjects(found)
        if (found.length > 0) setSubjectId(found[0].id)
      })
      .catch((failure) => !cancelled && setError((failure as Error).message))
    return () => {
      cancelled = true
    }
  }, [])

  // The subject's numbers follow the chosen subject; an answer for an earlier choice is dropped.
  useEffect(() => {
    if (scope !== 'subject' || subjectId === null) return
    let cancelled = false
    audioPreview(choiceOf('subject', subjectId))
      .then((preview) => !cancelled && setPreviews((now) => ({ ...now, subject: preview })))
      .catch((failure) => !cancelled && setError((failure as Error).message))
    return () => {
      cancelled = true
    }
  }, [scope, subjectId])

  function pickSubject(id: number) {
    if (id !== subjectId) setPreviews((now) => ({ ...now, subject: undefined }))
    setSubjectId(id)
    setScope('subject')
    setWarning(null)
  }

  function pickScope(which: AudioScope) {
    setScope(which)
    setWarning(null)
  }

  const current = previews[scope]
  useEscape(onClose)

  async function confirm() {
    if (!current) return
    setBusy(true)
    setError(null)
    try {
      onDone(await deleteRecordings(choiceOf(scope, subjectId), current.fingerprint))
    } catch (failure) {
      const shown = failure instanceof ApiError ? changedPreview(failure.detail) : null
      if (shown) {
        setPreviews((now) => ({ ...now, [scope]: shown }))
        setWarning((failure as Error).message)
      } else {
        setError((failure as Error).message)
      }
    } finally {
      setBusy(false)
    }
  }

  const counts = (preview: AudioPreview | undefined, long: boolean) =>
    preview
      ? long
        ? s.optionCounts(recordingsOf(preview.recordings), formatBytes(preview.bytes))
        : s.optionShort(preview.recordings, formatBytes(preview.bytes))
      : ''

  return (
    <div className="scrim">
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="audio-title">
        <h2 id="audio-title">{s.deleteTitle}</h2>
        {error && <Notice kind="error">{error}</Notice>}
        {warning && <Notice kind="warn">{warning}</Notice>}
        <fieldset>
          <legend>{s.which}</legend>
          <div className="options">
            <label className="choice opt">
              <input
                type="radio"
                name="audio-scope"
                checked={scope === 'older_than'}
                onChange={() => pickScope('older_than')}
              />{' '}
              <span>
                <strong>{s.olderThan(OLDER_THAN_DAYS)}</strong>{' '}
                <span className="num">{counts(previews.older_than, true)}</span>
              </span>
            </label>
            <label className="choice opt">
              <input
                type="radio"
                name="audio-scope"
                checked={scope === 'all'}
                onChange={() => pickScope('all')}
              />{' '}
              <span>
                <strong>{s.allRecordings}</strong> <span className="num">{counts(previews.all, false)}</span>
              </span>
            </label>
            <label className="choice opt">
              <input
                type="radio"
                name="audio-scope"
                checked={scope === 'subject'}
                onChange={() => pickScope('subject')}
              />{' '}
              <span>
                <strong>{s.oneSubject}</strong>{' '}
                {scope === 'subject' && <span className="num">{counts(previews.subject, false)}</span>}
                <div className="mt-2">
                  <select
                    className="select content"
                    aria-label={s.subject}
                    value={subjectId ?? ''}
                    onChange={(event) => pickSubject(Number(event.target.value))}
                  >
                    {subjects.map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {subject.name}
                      </option>
                    ))}
                  </select>
                </div>
              </span>
            </label>
          </div>
        </fieldset>
        <p>{s.stays}</p>
        <p className="small muted">{s.stillNeeded}</p>
        <div className="actions">
          <button className="btn" type="button" onClick={onClose}>
            {s.cancel}
          </button>
          <button
            className="btn danger solid"
            type="button"
            disabled={busy || !current || current.recordings === 0}
            onClick={confirm}
          >
            {s.deleteButton(recordingsOf(current?.recordings ?? 0))}
          </button>
        </div>
      </section>
    </div>
  )
}
