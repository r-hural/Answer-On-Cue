import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import {
  type DocumentDetail,
  type Subject,
  type VersionChange,
  applyVersion,
  discardVersion,
  getDocument,
  getSubject,
  previewVersion,
} from './api'
import { DropZone } from './DocumentsPanel'
import { Notice } from '../../ui/Notice'
import { libraryStrings } from './strings'

const s = libraryStrings.versions

/** The staged preview in a 409 answer: the summary changed since it was shown. */
function changedPreview(failure: unknown): VersionChange | null {
  if (!(failure instanceof ApiError) || failure.status !== 409) return null
  const detail = failure.detail as { preview?: VersionChange } | undefined
  return detail?.preview ?? null
}

function ChangeTable({ change }: { change: VersionChange }) {
  return (
    <table className="table">
      <tbody>
        <tr>
          <td>{s.rows.unchanged}</td>
          <td className="r num">{change.unchanged}</td>
        </tr>
        <tr>
          <td>{s.rows.changed}</td>
          <td className="r num">{change.changed}</td>
        </tr>
        <tr>
          <td>{s.rows.removed}</td>
          <td className="r num">{change.removed}</td>
        </tr>
        <tr>
          <td>{s.rows.new}</td>
          <td className="r num">{change.new}</td>
        </tr>
        <tr>
          <td>{s.rows.exclusions}</td>
          <td className="r num">{s.ofTotal(change.exclusions_carried, change.exclusions_total)}</td>
        </tr>
        {change.topics_without_material > 0 && (
          <tr>
            <td>{s.rows.topicsWithout}</td>
            <td className="r num">{change.topics_without_material}</td>
          </tr>
        )}
        <tr>
          <td>
            <strong>{s.rows.questions}</strong>
          </td>
          <td className="r num">
            <strong>{change.questions_to_review}</strong>
          </td>
        </tr>
      </tbody>
    </table>
  )
}

export function NewVersionPage() {
  const params = useParams()
  const subjectId = Number(params.subjectId)
  const documentId = Number(params.documentId)
  const navigate = useNavigate()
  const [subject, setSubject] = useState<Subject | null>(null)
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reading, setReading] = useState<string | null>(null)
  const [change, setChange] = useState<VersionChange | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // The token still staged on the server: discarded when the page goes away without Create.
  const staged = useRef<string | null>(null)

  useEffect(
    () => () => {
      if (staged.current) void discardVersion(documentId, staged.current).catch(() => undefined)
      staged.current = null
    },
    [documentId],
  )

  useEffect(() => {
    let cancelled = false
    Promise.all([getSubject(subjectId), getDocument(documentId)])
      .then(([nextSubject, nextDetail]) => {
        if (cancelled) return
        setSubject(nextSubject)
        setDetail(nextDetail)
      })
      .catch((failure: Error) => !cancelled && setLoadError(failure.message))
    return () => {
      cancelled = true
    }
  }, [subjectId, documentId])

  const subjectHref = `/library/${subjectId}`
  const documentHref = `${subjectHref}?document=${documentId}`

  async function choose(file: File) {
    setReading(file.name)
    setError(null)
    const previous = staged.current
    staged.current = null
    setChange(null)
    try {
      if (previous) await discardVersion(documentId, previous).catch(() => undefined)
      const next = await previewVersion(documentId, file)
      staged.current = next.token
      setChange(next)
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setReading(null)
    }
  }

  async function create() {
    if (!change) return
    setCreating(true)
    setError(null)
    try {
      await applyVersion(documentId, change.token, change.fingerprint)
      staged.current = null
      navigate(change.questions_to_review > 0 ? `${subjectHref}/questions?filter=review` : documentHref)
    } catch (failure) {
      const fresh = changedPreview(failure)
      if (fresh) {
        staged.current = fresh.token
        setChange(fresh)
      }
      setError((failure as Error).message)
      setCreating(false)
    }
  }

  async function cancel() {
    const token = staged.current
    staged.current = null
    if (token) await discardVersion(documentId, token).catch(() => undefined)
    navigate(documentHref)
  }

  if (loadError)
    return (
      <Notice kind="error">
        <p>{loadError}</p>
      </Notice>
    )
  if (!subject || !detail) return <p className="muted">{libraryStrings.loading}</p>

  const latest = detail.latest_version
  const n = change?.questions_to_review ?? 0
  const readable = change?.status === 'imported'
  return (
    <>
      <nav className="crumbs wrap-any" aria-label="Breadcrumb">
        <Link to="/library">{libraryStrings.subject.back}</Link> <span aria-hidden="true">›</span>{' '}
        <Link className="content" to={subjectHref}>
          {subject.name}
        </Link>{' '}
        <span aria-hidden="true">›</span>{' '}
        <Link className="file-name" to={documentHref}>
          {latest.filename}
        </Link>{' '}
        <span aria-hidden="true">›</span> <span>{s.crumb}</span>
      </nav>
      <div className="page-head">
        <div className="min-w-0">
          <h1>{s.title}</h1>
          <p className="num">
            <span className="file-name">
              {latest.filename}
            </span>
            {s.meta(latest.version, latest.page_count, latest.passage_count)}
          </p>
        </div>
      </div>

      <div className="cols-2">
        <section aria-labelledby="s1-h">
          <h2 id="s1-h" className="actions mb-4">
            <span className="step-n" aria-current={change ? undefined : 'step'}>
              1
            </span>
            {s.step1}
          </h2>
          <DropZone uploading={reading !== null || creating} onFile={choose} />
          {(reading || change) && (
            <ul className="rows compact mt-4">
              <li>
                <div>
                  <span className="title file-name">
                    {reading ?? change?.filename}
                  </span>
                  {change?.page_count != null && <p className="meta num">{s.pages(change.page_count)}</p>}
                  {change?.message && <p className="meta">{change.message}</p>}
                </div>
                {reading ? (
                  <span className="chip accent">{s.reading}</span>
                ) : change?.status === 'imported' ? (
                  <span className="chip ok">{s.read}</span>
                ) : (
                  <span className="chip error">{s.notRead}</span>
                )}
              </li>
            </ul>
          )}
          <p className="small muted mt-4">{s.keepsOld}</p>
        </section>

        <section aria-labelledby="s2-h">
          <h2 id="s2-h" className="actions mb-4">
            <span className="step-n" aria-current={change ? 'step' : undefined}>
              2
            </span>
            {s.step2}
          </h2>
          {error && (
            <Notice kind="error" className="mb-4">
              <p>{error}</p>
            </Notice>
          )}
          {change ? (
            <>
              <ChangeTable change={change} />
              {n > 0 && (
                <p className="mt-4">
                  {s.marked.before(n)}
                  <span className="chip warn">{s.marked.chip}</span>
                  {n === 1 ? s.marked.after : s.marked.afterMany}
                </p>
              )}
            </>
          ) : (
            <p className="muted">{s.chooseFirst}</p>
          )}
          {change && !readable && (
            <p id="not-readable" className="small muted mt-4">
              {s.notReadable}
            </p>
          )}
          <div className="actions mt-6">
            {change && (
              <button
                className="btn primary"
                type="button"
                disabled={creating || !readable}
                aria-describedby={readable ? undefined : 'not-readable'}
                onClick={() => void create()}
              >
                {s.create(change.new_version, n)}
              </button>
            )}
            <button className="btn" type="button" onClick={() => void cancel()}>
              {s.cancel}
            </button>
          </div>
        </section>
      </div>
    </>
  )
}
