import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { LanguageChip } from '../language/LanguageChip'
import { type ContentLanguage, type Subject, createSubject, listLanguages, listSubjects } from './api'
import { LanguageField } from './LanguageField'
import { Notice } from '../../ui/Notice'
import { libraryStrings } from './strings'
import { plNbsp } from '../../i18n/polish'

const s = libraryStrings.list

function EmptyShelf() {
  return (
    <svg
      className="mb-6 empty-art"
      viewBox="0 0 120 72"
      width="120"
      height="72"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 4h108M6 36h108M6 68h108M10 4v64M110 4v64" />
      <path d="M22 34V16h8v18M34 34V20h7v14M88 66V48h8v18" strokeDasharray="2 3" />
    </svg>
  )
}

export function SubjectListPage() {
  const [subjects, setSubjects] = useState<Subject[] | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [languages, setLanguages] = useState<ContentLanguage[]>([])
  const [language, setLanguage] = useState('')
  const [languageError, setLanguageError] = useState(false)

  useEffect(() => {
    let cancelled = false
    listSubjects()
      .then((list) => !cancelled && setSubjects(list))
      .catch(() => !cancelled && setError(libraryStrings.loadError))
    listLanguages()
      .then((list) => !cancelled && setLanguages(list))
      .catch(() => !cancelled && setLanguageError(true))
    return () => {
      cancelled = true
    }
  }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    try {
      setError(null)
      const created = await createSubject(name.trim(), description.trim(), language)
      setSubjects((current) => [...(current ?? []), created])
      setName('')
      setDescription('')
      setLanguage('')
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>{libraryStrings.title}</h1>
      </div>
      {error && (
        <Notice kind="error" className="mb-8">
          <p>{error}</p>
        </Notice>
      )}
      {languageError && (
        <Notice kind="error" className="mb-8">
          <p>{libraryStrings.languages.loadError}</p>
        </Notice>
      )}
      <div className="cols-main">
        {subjects !== null && subjects.length === 0 ? (
          <section className="empty" aria-labelledby="subjects-h">
            <EmptyShelf />
            <h2 id="subjects-h">{s.empty}</h2>
            <p>{s.emptyHelp}</p>
          </section>
        ) : (
          <section aria-labelledby="subjects-h">
            <div className="section-head">
              <h2 id="subjects-h">{s.subjects}</h2>
              {subjects && <span className="muted small">{s.count(subjects.length)}</span>}
            </div>
            {subjects === null && !error && <p className="muted">{libraryStrings.loading}</p>}
            {subjects && (
              <ul className="rows stack-sm">
                {subjects.map((subject) => (
                  <li key={subject.id}>
                    <div>
                      <Link className="title content" to={`/library/${subject.id}`}>
                        {subject.name}
                      </Link>
                      {subject.description && (
                        <p className="desc content">
                          {plNbsp(subject.description)}
                        </p>
                      )}
                      <p className="meta num">
                        <LanguageChip code={subject.language} languages={languages} />
                        {subject.document_count === 0 && <span className="chip">{s.noDocuments}</span>}
                        <span>{s.documents(subject.document_count)}</span>
                      </p>
                    </div>
                    <div className="end">
                      <Link className="btn small" to={`/library/${subject.id}`}>
                        {libraryStrings.subject.open}
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        <form className="box tint" aria-labelledby="new-h" onSubmit={create}>
          <h2 className="mb-5" id="new-h">
            {s.newSubject}
          </h2>
          <div className="field">
            <label className="label" htmlFor="subject-name">
              {s.name}
            </label>
            <input
              className="input content"
              id="subject-name"
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="subject-description">
              {s.description} <span className="muted">{s.optional}</span>
            </label>
            <textarea
              className="textarea content"
              id="subject-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <LanguageField id="subject-language" languages={languages} value={language} onChange={setLanguage} />
          <div className="actions mt-6">
            <button className="btn primary" type="submit" disabled={!name.trim() || !language}>
              {s.create}
            </button>
          </div>
        </form>
      </div>
    </>
  )
}
