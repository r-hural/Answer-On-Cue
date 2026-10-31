import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { formatBytes } from '../settings/storage'
import { useEscape } from '../settings/useEscape'
import { type DeletionPreview, deleteSubject, deletionPreview } from './api'
import { libraryStrings } from './strings'

const s = libraryStrings.remove
const d = libraryStrings.deletion

const noun = (count: number, [one, many]: readonly string[]) => `${count} ${count === 1 ? one : many}`

/** The summary a 409 answer carries: what the subject holds now. */
function changedSummary(detail: unknown): DeletionPreview | null {
  if (detail && typeof detail === 'object' && 'preview' in detail) {
    return (detail as { preview: DeletionPreview }).preview
  }
  return null
}

export function DeleteSubject({
  subjectId,
  name,
  onDeleted,
}: {
  subjectId: number
  name: string
  onDeleted: () => void
}) {
  const [preview, setPreview] = useState<DeletionPreview | null>(null)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function open() {
    try {
      setError(null)
      setPreview(await deletionPreview(subjectId))
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  async function confirm() {
    if (!preview) return
    try {
      await deleteSubject(subjectId, preview.fingerprint)
      onDeleted()
    } catch (failure) {
      const changed = failure instanceof ApiError ? changedSummary(failure.detail) : null
      if (changed) {
        // What the subject holds changed since the summary was shown: show the new one and
        // make the learner confirm again.
        setPreview(changed)
        setTyped('')
      }
      setError((failure as Error).message)
    }
  }

  function cancel() {
    setPreview(null)
    setTyped('')
    setError(null)
  }

  // Escape closes the dialog wherever the focus is (also on the page body after a click on text).
  useEscape(() => {
    if (preview) cancel()
  })

  return (
    <section className="box" aria-labelledby="delete-title">
      <h2 id="delete-title">{s.button}</h2>
      <p className="small muted mb-4">{s.help}</p>
      <button className="btn danger" type="button" onClick={open}>
        {s.button}
      </button>
      {error && !preview && (
        <p className="small text-error mt-2" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div className="scrim">
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
            <h2 id="delete-dialog-title">
              {s.dialogTitle}{' '}
              <span className="content">
                „{name}”
              </span>
              ?
            </h2>
            <p>{d.intro}</p>
            <table className="table">
              <tbody>
                <tr>
                  <td>{d.documents}</td>
                  <td className="r">
                    {d.documentsValue(
                      preview.documents,
                      noun(preview.versions, d.version),
                      noun(preview.passages, d.passage),
                    )}
                  </td>
                </tr>
                <tr>
                  <td>{d.topics}</td>
                  <td className="r">
                    {d.topicsValue(noun(preview.topics, d.topic), noun(preview.objectives, d.objective))}
                  </td>
                </tr>
                <tr>
                  <td>{d.questions}</td>
                  <td className="r">
                    {d.questionsValue(preview.questions, noun(preview.question_versions, d.version))}
                  </td>
                </tr>
                <tr>
                  <td>{d.rounds}</td>
                  <td className="r">
                    {d.roundsValue(noun(preview.rounds, d.round), noun(preview.answers, d.answer))}
                  </td>
                </tr>
                <tr>
                  <td>{d.recordings}</td>
                  <td className="r">
                    {d.recordingsValue(preview.recordings, formatBytes(preview.recording_bytes))}
                  </td>
                </tr>
                <tr>
                  <td>{d.coverage}</td>
                  <td className="r">{d.coverageAll}</td>
                </tr>
              </tbody>
            </table>
            <p>{d.usage}</p>
            <p className="small muted">
              {d.keepCopy} <Link to="/settings#storage">{d.exportLink}</Link> {d.or}{' '}
              <Link to="/settings#storage">{d.backupLink}</Link> {d.first}
            </p>
            <div className="field">
              <label className="label" htmlFor="delete-confirm">
                {s.typeName(name)}
              </label>
              <input
                className="input content"
                id="delete-confirm"
                type="text"
                autoComplete="off"
                autoFocus
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
              />
            </div>
            {error && (
              <p className="small text-error" role="alert">
                {error}
              </p>
            )}
            <div className="actions">
              <button className="btn" type="button" onClick={cancel}>
                {s.cancel}
              </button>
              <button
                className="btn danger solid"
                type="button"
                onClick={confirm}
                disabled={typed.trim() !== name}
              >
                {s.confirm}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  )
}
