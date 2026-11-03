import { usd } from '../costs/format'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import type { FeedbackTiming, RoundSourcePolicy } from '../rounds/api'
import { bandClass, bandKey, BANDS, dateTime, metricOrUnavailable, score, seconds, timeOnly } from '../rounds/feedback/format'
import { getHistory, type HistoryRow } from '../rounds/reports'
import { roundStrings } from '../rounds/strings'
import { roundPolicyLabel } from '../rounds/sourcePolicy'
import { ProgressTabs } from './ProgressTabs'
import { progressStrings as t } from './strings'
import { useChapters, type SubjectWithChapters } from './useChapters'
import { Loading } from '../../ui/Spinner'

type SubjectRef = SubjectWithChapters

const OPEN = ['active', 'paused']

function Row({ row }: { row: HistoryRow }) {
  const open = OPEN.includes(row.status)
  const early = row.status === 'abandoned'
  const href = open ? `/rounds/${row.round_id}` : `/rounds/${row.round_id}/summary`
  const hasScore = row.primary_score !== null
  return (
    <li>
      <div>
        <div className="head">
          <Link className="title num" to={href}>
            {dateTime(row.started_at)}
          </Link>
          <span className="subject content">
            {plNbsp(row.subject_name)}
          </span>
        </div>
        <div className="meta">
          {row.round_type === 'retry' && row.parent_started_at ? (
            <span>{t.repetition.retryOf(timeOnly(row.parent_started_at))}</span>
          ) : (
            <span>
              {row.round_type === 'adaptive' ? t.adaptive.adaptive : t.standard} · {row.feedback_timing === 'exam' ? t.exam : t.practice}
              {/* My materials, or the legacy expanded rounds; All questions rounds (and old servers) carry no label */}
              {(row.source_policy === 'materials' || row.source_policy === 'expanded') && (
                <> · {roundPolicyLabel(row.source_policy)}</>
              )}
            </span>
          )}
          {row.chapter_id != null &&
            (row.chapter_name == null ? (
              <span className="chip">{t.chapters.unnamed}</span>
            ) : (
              <span className="chip content">
                {plNbsp(row.chapter_name)}
              </span>
            ))}
          <span className="num">{early || open ? t.questionsOf(row.asked, row.total) : t.questions(row.total)}</span>
          {early && <span className="chip warn">{t.endedEarly}</span>}
          {open && <span className="chip warn">{t.notFinished}</span>}
        </div>
      </div>
      <dl className="rstats">
        <div>
          <dt>{t.knowledge}</dt>
          <dd className="score">
            {hasScore ? score(row.primary_score as number) : t.dash}
            {hasScore && row.provisional && (
              <>
                {' '}
                <small>{t.provisional}</small>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>{t.bands}</dt>
          <dd>
            <div className="band-counts">
              {BANDS.map((band) => (
                <span key={band} className={`${bandKey(band)}${row.band_distribution[band] === 0 ? ' zero' : ''}`}>
                  {row.band_distribution[band]}
                  <i className="visually-hidden"> {t.bandWords[band]}</i>
                </span>
              ))}
            </div>
          </dd>
        </div>
        <div>
          <dt>{t.prepMedian}</dt>
          <dd>{row.median_preparation_used_ms === null ? t.dash : metricOrUnavailable(row.median_preparation_used_ms, undefined, seconds)}</dd>
        </div>
        <div>
          <dt>{t.fluency}</dt>
          <dd>{row.mean_fluency === null ? t.dash : score(row.mean_fluency)}</dd>
        </div>
        {row.ai_cost_usd != null && (
          <div>
            <dt>{t.costs.aiCost}</dt>
            <dd>{usd(row.ai_cost_usd)}</dd>
          </div>
        )}
      </dl>
    </li>
  )
}

/** Past rounds, newest first, with the subject and feedback-timing filters. */
export function HistoryPage() {
  const [subjects, setSubjects] = useState<SubjectRef[]>([])
  const [subjectId, setSubjectId] = useState<number | null>(null)
  const [timing, setTiming] = useState<FeedbackTiming | null>(null)
  const [sourcePolicy, setSourcePolicy] = useState<RoundSourcePolicy | null>(null)
  const [chosenChapter, setChosenChapter] = useState<number | null>(null)
  const { chapters } = useChapters(subjects.find((subject) => subject.id === subjectId))
  // the chapter filter exists only for a chosen subject with chapters
  const chapterId = chapters?.some((chapter) => chapter.id === chosenChapter) ? chosenChapter : null
  const [rows, setRows] = useState<HistoryRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiGet<SubjectRef[]>('/subjects')
      .then((list) => !cancelled && setSubjects(list))
      .catch(() => undefined) // the filter then only offers "All subjects"
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getHistory({ subjectId, feedbackTiming: timing, sourcePolicy, chapterId })
      .then((found) => {
        if (cancelled) return
        setRows(found)
        setError(null)
      })
      .catch((failure: unknown) => !cancelled && setError(failure instanceof Error ? failure.message : t.loadError))
    return () => {
      cancelled = true
    }
  }, [subjectId, timing, sourcePolicy, chapterId])

  const filtered = subjectId !== null || timing !== null || sourcePolicy !== null || chapterId !== null

  return (
    <>
      <div className="page-head mb-6">
        <h1>{t.title}</h1>
      </div>

      <ProgressTabs current="history" />

      <form className="filters" aria-label={t.filtersLabel} onSubmit={(event) => event.preventDefault()}>
        <div className="field">
          <label className="label" htmlFor="f-subject">
            {t.subject}
          </label>
          <select
            className="select"
            id="f-subject"
            value={subjectId ?? ''}
            onChange={(event) => {
              setSubjectId(event.target.value === '' ? null : Number(event.target.value))
              setChosenChapter(null)
            }}
          >
            <option value="">{t.allSubjects}</option>
            {subjects.map((subject) => (
              <option key={subject.id} value={subject.id}>
                {subject.name}
              </option>
            ))}
          </select>
        </div>
        {chapters && (
          <div className="field">
            <label className="label" htmlFor="f-chapter">
              {t.chapters.chapter}
            </label>
            <select
              className="select"
              id="f-chapter"
              value={chapterId ?? ''}
              onChange={(event) => setChosenChapter(event.target.value === '' ? null : Number(event.target.value))}
            >
              <option value="">{t.chapters.all}</option>
              {chapters.map((chapter) =>
                chapter.name === null ? (
                  <option key={chapter.id} value={chapter.id}>
                    {t.chapters.unnamed}
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
        <div className="field">
          <label className="label" htmlFor="f-feedback">
            {t.feedback}
          </label>
          <select
            className="select"
            id="f-feedback"
            value={timing ?? ''}
            onChange={(event) => setTiming(event.target.value === '' ? null : (event.target.value as FeedbackTiming))}
          >
            <option value="">{t.allFeedback}</option>
            <option value="practice">{t.practice}</option>
            <option value="exam">{t.exam}</option>
          </select>
        </div>
        <div className="field">
          <label className="label" htmlFor="f-sources">
            {t.sources.label}
          </label>
          <select
            className="select"
            id="f-sources"
            value={sourcePolicy ?? ''}
            onChange={(event) =>
              setSourcePolicy(event.target.value === '' ? null : (event.target.value as RoundSourcePolicy))
            }
          >
            <option value="">{t.sources.all}</option>
            <option value="materials">{roundPolicyLabel('materials')}</option>
            <option value="all">{roundPolicyLabel('all')}</option>
          </select>
        </div>
      </form>

      {error && <Notice kind="error">{error}</Notice>}
      {!rows && !error && <Loading>{t.loading}</Loading>}

      {rows && rows.length === 0 && (
        <section className="empty" aria-labelledby="h-empty">
          <h2 id="h-empty">{filtered ? t.emptyFiltered : t.emptyTitle}</h2>
          {!filtered && <p>{t.emptyText}</p>}
          <Link className="btn primary" to="/practice">
            {t.newRound}
          </Link>
        </section>
      )}

      {rows && rows.length > 0 && (
        <>
          <h2 className="visually-hidden">{t.rounds}</h2>
          <ul className="rows rounds">
            {rows.map((row) => (
              <Row key={row.round_id} row={row} />
            ))}
          </ul>
          <div className="band-key" aria-label={t.bandKey}>
            <span>{t.bandsInOrder}</span>
            {BANDS.map((band) => (
              <span key={band} className={bandClass(band)}>
                {roundStrings.exam.feedback.bands[band]}
              </span>
            ))}
          </div>
        </>
      )}
    </>
  )
}
