import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Notice } from '../../ui/Notice'
import { type ContentLanguage, createSubject, deleteSubject, listLanguages, uploadDocument } from '../library/api'
import { LanguageField } from '../library/LanguageField'
import { libraryStrings } from '../library/strings'
import { UploadIcon } from '../library/UploadIcon'
import { QuickHeader } from './QuickHeader'
import { nameCandidates, subjectNameFromFile } from './quickState'
import { quickStrings } from './strings'

const s = quickStrings.upload

/** Creates the subject for a file; a taken name gets " (2)" … " (9)". */
async function createNamedSubject(name: string, language: string) {
  for (const candidate of nameCandidates(name)) {
    try {
      return await createSubject(candidate, '', language)
    } catch (failure) {
      if (!(failure instanceof ApiError) || failure.status !== 409) throw failure
    }
  }
  throw new Error(quickStrings.upload.nameTaken)
}

export function QuickUploadPage() {
  const navigate = useNavigate()
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [languages, setLanguages] = useState<ContentLanguage[] | null>(null)
  const [languageError, setLanguageError] = useState(false)
  const [picked, setPicked] = useState('')

  useEffect(() => {
    let cancelled = false
    listLanguages()
      .then((list) => !cancelled && setLanguages(list))
      .catch(() => !cancelled && setLanguageError(true))
    return () => {
      cancelled = true
    }
  }, [])

  // One enabled language is used as is; with several, the learner chooses.
  const enabled = (languages ?? []).filter((row) => row.enabled)
  const language = enabled.length === 1 ? enabled[0].code : picked

  function choose(next: File | undefined) {
    if (next && !busy) {
      setFile(next)
      setError(null)
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setOver(false)
    choose(event.dataTransfer.files[0])
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file || busy || !language) return
    setBusy(true)
    setError(null)
    let subjectId: number | null = null
    try {
      const subject = await createNamedSubject(subjectNameFromFile(file.name), language)
      subjectId = subject.id
      await uploadDocument(subject.id, file)
      navigate(`/quick/${subject.id}`)
    } catch (failure) {
      if (subjectId !== null) await deleteSubject(subjectId).catch(() => undefined)
      setError((failure as Error).message)
      setBusy(false)
    }
  }

  return (
    <>
      <QuickHeader stage={0} />
      <div className="cols-main">
        <form onSubmit={submit} aria-labelledby="up-h">
          <h2 id="up-h">{s.heading}</h2>
          <div
            className={over ? 'drop over' : 'drop'}
            onDragOver={(event) => {
              event.preventDefault()
              if (!busy) setOver(true)
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <UploadIcon />
            <p>{s.drop}</p>
            <label className="btn small">
              {s.choose}
              <input
                ref={input}
                type="file"
                hidden
                accept=".pdf,.docx,.txt"
                onChange={(event) => {
                  choose(event.target.files?.[0])
                  event.target.value = ''
                }}
              />
            </label>
          </div>
          <p className="hint mt-3">{s.hint}</p>
          {error && (
            <Notice kind="error" className="mt-4">
              <p>{error}</p>
            </Notice>
          )}
          {languageError && (
            <Notice kind="error" className="mt-4">
              <p>{libraryStrings.languages.loadError}</p>
            </Notice>
          )}
          {enabled.length > 1 && (
            <div className="mt-4">
              <LanguageField
                id="quick-language"
                languages={enabled}
                value={picked}
                onChange={setPicked}
                disabled={busy}
              />
            </div>
          )}
          <div className="actions spaced">
            <button className="btn primary" type="submit" disabled={!file || busy || !language}>
              {busy ? s.uploading : file ? s.file(file.name) : s.button}
            </button>
            <Link className="btn" to="/">
              {quickStrings.cancel}
            </Link>
          </div>
        </form>

        <aside className="side" aria-label={s.side}>
          <section className="box tint" aria-labelledby="next-h">
            <h2 id="next-h">{s.next}</h2>
            <ol className="list">
              {s.nextSteps.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            <p className="small muted mt-4">{s.failedNote}</p>
          </section>
        </aside>
      </div>
    </>
  )
}
