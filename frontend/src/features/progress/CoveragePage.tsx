import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Notice } from '../../ui/Notice'
import { dateOnly } from '../rounds/feedback/format'
import {
  getSubjectCoverage,
  layerOf,
  type CoverageLayer,
  type ObjectiveCoverage,
  type SubjectCoverage,
  type TopicCoverage,
} from './coverage'
import { KEY_ORDER, prep, recentFactual, STATUSES, statusClass } from './coverageFormat'
import { roundPolicyLabel } from '../rounds/sourcePolicy'
import { ProgressTabs } from './ProgressTabs'
import { progressStrings } from './strings'
import { useChapters, type SubjectWithChapters } from './useChapters'
import { ContentLanguageProvider, useContentLanguage } from '../language/ContentLanguage'

const t = progressStrings.coverage
const ch = progressStrings.chapters
const TOPICS_SHOWN = 20

/** The subjects list carries each subject's content language (missing on an older server: Polish). */
type SubjectRef = SubjectWithChapters & { language?: string }

/** `&chapter=<id>` when a chapter is chosen, for links that keep it. */
const chapterSuffix = (chapterId: number | null) => (chapterId === null ? '' : `&chapter=${chapterId}`)

function Status({ line }: { line: Pick<ObjectiveCoverage, 'status'> }) {
  return <span className={`status ${statusClass(line.status)}`}>{t.status[line.status]}</span>
}

function Line({ line, delivery = true }: { line: ObjectiveCoverage; delivery?: boolean }) {
  const cl = useContentLanguage()
  return (
    <tr>
      {line.text === null ? (
        <td data-label={t.cells.objective} className="obj">
          {t.wholeTopic}
        </td>
      ) : (
        <td data-label={t.cells.objective} className="obj content" lang={cl.bcp47}>
          {cl.typeset(line.text)}
        </td>
      )}
      <td data-label={t.columns.status}>
        <Status line={line} />
        {line.assisted && (
          <>
            {' '}
            <span className="note">{t.withHelp}</span>
          </>
        )}
      </td>
      <td data-label={t.columns.answers} className="r num">
        {line.answers}
      </td>
      <td data-label={t.columns.last} className="num">
        {line.last_practised_at ? dateOnly(line.last_practised_at) : progressStrings.dash}
      </td>
      <td data-label={t.columns.recent} className="r">
        {recentFactual(line.recent_factual)}
      </td>
      {delivery && (
        <td data-label={t.columns.delivery} className="note">
          {prep(line.median_preparation_used_ms)}
        </td>
      )}
    </tr>
  )
}

function Topic({ topic, layer = 'all' }: { topic: TopicCoverage; layer?: CoverageLayer }) {
  const cl = useContentLanguage()
  return (
    <>
      <tr>
        <th colSpan={6} scope="colgroup">
          <Link
            className="content-title content"
            lang={cl.bcp47}
            to={`/progress/topics/${topic.topic_id}${layer === 'materials' ? '?layer=materials' : ''}`}
          >
            {cl.typeset(topic.title)}
          </Link>
        </th>
      </tr>
      {topic.objectives.map((line) => (
        <Line key={line.objective_id ?? 'topic'} line={line} />
      ))}
    </>
  )
}

/** "My materials" | "All questions": the two coverage layers of a subject. */
function SourcesSwitch({
  subjectId,
  layer,
  chapterId = null,
}: {
  subjectId: number
  layer: CoverageLayer
  chapterId?: number | null
}) {
  return (
    <div className="group">
      <span className="label" id="sources-label">
        {progressStrings.sources.label}
      </span>
      <nav className="seg" aria-labelledby="sources-label">
        <Link
          to={`?subject=${subjectId}&layer=materials${chapterSuffix(chapterId)}`}
          aria-current={layer === 'materials' ? 'page' : undefined}
        >
          {roundPolicyLabel('materials')}
        </Link>
        <Link
          to={`?subject=${subjectId}${chapterSuffix(chapterId)}`}
          aria-current={layer === 'all' ? 'page' : undefined}
        >
          {roundPolicyLabel('all')}
        </Link>
      </nav>
    </div>
  )
}

function CoverageMap({ coverage, chapterId }: { coverage: SubjectCoverage; chapterId: number | null }) {
  const layer: CoverageLayer = coverage.layer === 'materials' ? 'materials' : 'all'
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? coverage.topics : coverage.topics.slice(0, TOPICS_SHOWN)
  const hidden = coverage.topics.length - shown.length
  return (
    <>
      <div className="toolbar">
        <SourcesSwitch subjectId={coverage.subject_id} layer={layer} chapterId={chapterId} />
        <div className="group" aria-label={t.statusKey}>
          {KEY_ORDER.map((key) => (
            <span key={key} className={`status ${statusClass(key)}`}>
              {t.status[key]}
            </span>
          ))}
        </div>
      </div>

      <div className="scores mt-6" aria-label={t.countsLabel}>
        {STATUSES.map((key, index) => (
          <div key={key}>
            <div className="k">
              <span className={`status ${statusClass(key)}`}>{t.status[key]}</span>
            </div>
            <div className="v num">{coverage.line_counts[key]}</div>
            <div className="of">
              {index === 0 ? t.ofAll(coverage.line_total) : t.of(coverage.line_total)}
            </div>
          </div>
        ))}
      </div>

      <section className="section" aria-labelledby="map-h">
        <h2 id="map-h">{t.mapTitle}</h2>
        <p className="muted mb-5">{t.mapIntro}</p>
        <div className="table-wrap">
          <table className="table coverage-table">
            <thead>
              <tr>
                <th scope="col">{t.columns.objective}</th>
                <th scope="col">{t.columns.status}</th>
                <th scope="col" className="r">
                  {t.columns.answers}
                </th>
                <th scope="col">{t.columns.last}</th>
                <th scope="col" className="r">
                  {t.columns.recent}
                </th>
                <th scope="col">{t.columns.delivery}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((topic) => (
                <Topic key={topic.topic_id} topic={topic} layer={layer} />
              ))}
              {hidden > 0 && (
                <tr>
                  <td colSpan={6}>
                    <button className="btn link" type="button" onClick={() => setShowAll(true)}>
                      {t.showMore(hidden)}
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="actions spaced">
          <Link
            className="btn primary"
            to={`/practice?subject=${coverage.subject_id}&type=adaptive&sources=${layer}${chapterSuffix(chapterId)}`}
          >
            {progressStrings.adaptive.practiseWeak}
          </Link>
          <span className="muted small">{t.lines(coverage.line_total)}</span>
        </div>
      </section>
    </>
  )
}

/** The coverage map of one subject: status counts, then one line per learning objective. */
export function CoveragePage() {
  const [params, setParams] = useSearchParams()
  const [subjects, setSubjects] = useState<SubjectRef[] | null>(null)
  const [loaded, setLoaded] = useState<{
    id: number
    coverage: SubjectCoverage
    layer?: CoverageLayer
    chapterId: number | null
  } | null>(null)
  const [failure, setFailure] = useState<{ id: number | null; message: string } | null>(null)
  const subjectId = Number(params.get('subject')) || null
  const layer = layerOf(params.get('layer'))
  const { chapters, pending } = useChapters(subjects?.find((subject) => subject.id === subjectId))
  // ?chapter= counts only while the subject has chapters; one chapter asks exactly as before
  const wantedChapter = Number(params.get('chapter')) || null
  const chapterId = chapters?.some((chapter) => chapter.id === wantedChapter) ? wantedChapter : null
  // with ?chapter=, wait for the subject list and its chapters, so the unfiltered map is not asked first
  const chapterKnown = wantedChapter === null || (subjects !== null && !pending)

  useEffect(() => {
    let cancelled = false
    apiGet<SubjectRef[]>('/subjects')
      .then((list) => !cancelled && setSubjects(list))
      .catch((error: unknown) => {
        if (!cancelled) setFailure({ id: null, message: error instanceof Error ? error.message : t.loadError })
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (subjectId === null && subjects && subjects.length > 0) {
      setParams({ subject: String(subjects[0].id) }, { replace: true })
    }
  }, [subjectId, subjects, setParams])

  useEffect(() => {
    if (subjectId === null || !chapterKnown) return
    let cancelled = false
    getSubjectCoverage(subjectId, layer, chapterId)
      .then((coverage) => {
        if (cancelled) return
        setLoaded({ id: subjectId, coverage: { ...coverage, layer }, layer, chapterId })
        setFailure(null)
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure({ id: subjectId, message: error instanceof Error ? error.message : t.loadError })
      })
    return () => {
      cancelled = true
    }
  }, [subjectId, layer, chapterId, chapterKnown])

  const coverage =
    loaded && loaded.id === subjectId && loaded.layer === layer && loaded.chapterId === chapterId
      ? loaded.coverage
      : null
  const subjectLanguage = subjects?.find((subject) => subject.id === subjectId)?.language ?? null
  const error = failure && (failure.id === null || failure.id === subjectId) ? failure.message : null
  const noSubjects = subjects !== null && subjects.length === 0

  return (
    <>
      <div className="page-head mb-6">
        <h1>{progressStrings.title}</h1>
        {subjects && subjects.length > 0 && (
          <div className="field">
            <label className="visually-hidden" htmlFor="subject">
              {progressStrings.subject}
            </label>
            <select
              className="select content"
              id="subject"
              value={subjectId ?? ''}
              onChange={(event) =>
                setParams(
                  layer === 'materials' ? { subject: event.target.value, layer } : { subject: event.target.value },
                )
              }
            >
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {chapters && subjectId !== null && (
          <div className="field">
            <label className="label" htmlFor="chapter">
              {ch.chapter}
            </label>
            <select
              className="select content"
              id="chapter"
              value={chapterId ?? ''}
              onChange={(event) => {
                const next: Record<string, string> = { subject: String(subjectId) }
                if (layer === 'materials') next.layer = layer
                if (event.target.value !== '') next.chapter = event.target.value
                setParams(next)
              }}
            >
              <option value="">{ch.all}</option>
              {chapters.map((chapter) =>
                chapter.name === null ? (
                  <option key={chapter.id} value={chapter.id}>
                    {ch.unnamed}
                  </option>
                ) : (
                  <option key={chapter.id} value={chapter.id}>
                    {chapter.name}
                  </option>
                ),
              )}
            </select>
          </div>
        )}
      </div>

      <ProgressTabs current="coverage" subjectId={subjectId} />

      {error && <Notice kind="error">{error}</Notice>}
      {!error && !noSubjects && !coverage && <p role="status">{t.loading}</p>}

      {noSubjects && (
        <section className="empty" aria-labelledby="h-empty">
          <h2 id="h-empty">{t.noSubjectsTitle}</h2>
          <p>{t.noSubjectsText}</p>
          <Link className="btn primary" to="/library">
            {t.toLibrary}
          </Link>
        </section>
      )}

      {coverage && coverage.topics.length === 0 && (
        <section className="empty" aria-labelledby="h-empty">
          <h2 id="h-empty">{t.noTopicsTitle}</h2>
          <p>{t.noTopicsText}</p>
          <Link className="btn primary" to={`/library/${coverage.subject_id}`}>
            {t.toSubject}
          </Link>
        </section>
      )}

      {coverage && coverage.topics.length > 0 && (
        <ContentLanguageProvider code={subjectLanguage}>
          <CoverageMap key={coverage.subject_id} coverage={coverage} chapterId={chapterId} />
        </ContentLanguageProvider>
      )}
    </>
  )
}
