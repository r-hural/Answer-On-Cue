import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  type BackMatter,
  type DocumentDetail as DocumentInfo,
  PASSAGE_PAGE,
  type Passage,
  type Preview,
  getBackMatter,
  getDocument,
  getPreview,
  listPassages,
  setExclusion,
  setPassageExcluded,
} from './api'
import { Notice } from '../../ui/Notice'
import { libraryStrings } from './strings'
import { useContentLanguage } from '../language/ContentLanguage'

const s = libraryStrings.detail
const v = libraryStrings.versions
const PREVIEW_CHARS = 3000

const shortDate = (iso: string | undefined) =>
  iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : ''

/** "Version 2 of 2 · uploaded 20 Oct" and the entry to replace the document. */
function VersionHead({ info, subjectId }: { info: DocumentInfo; subjectId: number }) {
  const latest = info.latest_version
  return (
    <div className="section-head section">
      <p className="num">
        {v.versionOf(latest.version, info.version_count)}
        {latest.created_at && v.uploaded(shortDate(latest.created_at))}
      </p>
      <Link className="btn" to={`/library/${subjectId}/documents/${info.id}/new-version`}>
        {v.replace}
      </Link>
    </div>
  )
}

/** The document's versions, newest first, with the answers that still point to older ones. */
function VersionList({ info }: { info: DocumentInfo }) {
  const newest = [...info.versions].sort((a, b) => b.version - a.version)
  return (
    <section className="section" aria-labelledby="versions-h">
      <h2 id="versions-h">{v.versionsTitle}</h2>
      <ul className="rows compact">
        {newest.map((version) => {
          const used = (version.answer_count ?? 0) > 0
          const meta = [shortDate(version.created_at), used ? v.usedBy(version.answer_count ?? 0, version.round_count ?? 0) : '']
            .filter(Boolean)
            .join(' · ')
          return (
            <li key={version.id}>
              <div>
                <span className="title">{v.version(version.version)}</span>
                {meta && <p className="meta num">{meta}</p>}
              </div>
              {version.id === info.latest_version.id ? <span className="chip">{v.current}</span> : <span />}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

type BannerState = 'suggest' | 'applied' | 'hidden'

function BackMatterBanner({
  versionId,
  suggestion,
  onChanged,
}: {
  versionId: number
  suggestion: BackMatter
  onChanged: (ids: number[], excluded: boolean) => void
}) {
  const [state, setState] = useState<BannerState>(suggestion.already_excluded ? 'applied' : 'suggest')
  const [error, setError] = useState<string | null>(null)

  async function apply(excluded: boolean) {
    try {
      setError(null)
      await setExclusion(versionId, suggestion.passage_ids, excluded)
      onChanged(suggestion.passage_ids, excluded)
      setState(excluded ? 'applied' : 'suggest')
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  if (suggestion.count === 0 || state === 'hidden') return null
  return (
    <Notice kind={state === 'suggest' ? 'warn' : 'ok'} label="Footnotes and bibliography" className="mb-4">
      {state === 'suggest' ? (
        <>
          <p className="num">
            {s.backMatter(suggestion.count, suggestion.first_locator ?? '', suggestion.last_locator ?? '')}
          </p>
          <div className="actions">
            <button className="btn small" type="button" onClick={() => apply(true)}>
              {s.skipBackMatter}
            </button>
            <button className="btn small" type="button" onClick={() => setState('hidden')}>
              {s.keep}
            </button>
          </div>
        </>
      ) : (
        <>
          <p>{s.skipped(suggestion.count)}</p>
          <div className="actions">
            <button className="btn small" type="button" onClick={() => apply(false)}>
              {s.restore}
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="text-error mt-2" role="alert">
          {error}
        </p>
      )}
    </Notice>
  )
}

/** The extracted text as paragraphs; single line breaks of the source stay line breaks. */
function PreviewText({ text }: { text: string }) {
  const cl = useContentLanguage()
  return text.split(/\n{2,}/).map((block, b) => (
    <p key={b}>
      {block.split('\n').map((line, l) => (
        <span key={l}>
          {l > 0 && <br />}
          {cl.typeset(line)}
        </span>
      ))}
    </p>
  ))
}

/** Mount with `key={versionId}`: a new document starts from a clean state. */
export function DocumentDetail({ versionId, documentId }: { versionId: number; documentId?: number }) {
  const cl = useContentLanguage()
  const subjectId = Number(useParams().subjectId)
  const [info, setInfo] = useState<DocumentInfo | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [full, setFull] = useState(false)
  const [passages, setPassages] = useState<Passage[]>([])
  const [total, setTotal] = useState(0)
  const [suggestion, setSuggestion] = useState<BackMatter | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getPreview(versionId), listPassages(versionId, 0), getBackMatter(versionId)])
      .then(([nextPreview, page, back]) => {
        if (cancelled) return
        setPreview(nextPreview)
        setPassages(page.items)
        setTotal(page.total)
        setSuggestion(back)
      })
      .catch((failure: Error) => !cancelled && setError(failure.message))
    return () => {
      cancelled = true
    }
  }, [versionId])

  useEffect(() => {
    if (documentId === undefined) return
    let cancelled = false
    // the versions are extra: the passages stay usable when they cannot be loaded
    getDocument(documentId)
      .then((detail) => !cancelled && setInfo(detail))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [documentId])

  const loadMore = useCallback(async () => {
    try {
      const page = await listPassages(versionId, passages.length)
      setPassages((current) => [...current, ...page.items])
      setTotal(page.total)
    } catch (failure) {
      setError((failure as Error).message)
    }
  }, [versionId, passages.length])

  async function toggle(passage: Passage, excluded: boolean) {
    try {
      const saved = await setPassageExcluded(passage.id, excluded)
      setPassages((current) => current.map((p) => (p.id === saved.id ? saved : p)))
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  const bulkChanged = (ids: number[], excluded: boolean) =>
    setPassages((current) => current.map((p) => (ids.includes(p.id) ? { ...p, excluded } : p)))

  if (error)
    return (
      <p className="text-error section" role="alert">
        {error}
      </p>
    )
  if (!preview) return <p className="muted section">{libraryStrings.loading}</p>

  const shown = full ? preview.text : preview.text.slice(0, PREVIEW_CHARS)
  return (
    <>
      {info && <VersionHead info={info} subjectId={subjectId} />}
      <section className="section" aria-labelledby="preview-h">
        <h2 id="preview-h">{s.preview}</h2>
        <div className="text-preview" tabIndex={0} role="region" aria-label={s.previewRegion} lang={cl.bcp47}>
          <PreviewText text={shown} />
        </div>
        {!full && preview.text.length > PREVIEW_CHARS && (
          <div className="actions mt-3">
            <button className="btn small" type="button" onClick={() => setFull(true)}>
              {s.showMore}
            </button>
          </div>
        )}
      </section>

      <section className="section" id="passages" aria-labelledby="passages-h">
        <div className="section-head">
          <h2 id="passages-h">{s.passages}</h2>
          <span className="small muted num">{s.shown(passages.length, total)}</span>
        </div>
        <p className="muted mb-4">{s.passagesHelp}</p>
        {suggestion && (
          <BackMatterBanner
            key={`${versionId}-${suggestion.already_excluded}`}
            versionId={versionId}
            suggestion={suggestion}
            onChanged={bulkChanged}
          />
        )}
        <ul className="rows passages stack-sm">
          {passages.map((passage) => (
            <li key={passage.id} className={passage.excluded ? 'excluded' : undefined}>
              <div>
                <p className="loc num">
                  <span>{passage.locator}</span>
                  {passage.heading && (
                    <>
                      <span aria-hidden="true">·</span>
                      <span className="content" lang={cl.bcp47}>
                        {cl.typeset(passage.heading)}
                      </span>
                    </>
                  )}
                  {passage.excluded && <span className="chip outline">{s.excluded}</span>}
                </p>
                <p className="content-body" lang={cl.bcp47}>
                  {cl.typeset(passage.text.slice(0, 200))}
                  {passage.text.length > 200 ? '…' : ''}
                </p>
              </div>
              <label className="choice">
                <input
                  type="checkbox"
                  aria-label={s.skipPassage(passage.locator)}
                  checked={passage.excluded}
                  onChange={(event) => toggle(passage, event.target.checked)}
                />
                {s.skip}
              </label>
            </li>
          ))}
        </ul>
        {passages.length < total && (
          <div className="actions mt-4">
            <button className="btn" type="button" onClick={loadMore}>
              {s.moreItems} ({Math.min(PASSAGE_PAGE, total - passages.length)})
            </button>
          </div>
        )}
      </section>
      {info && <VersionList info={info} />}
    </>
  )
}
