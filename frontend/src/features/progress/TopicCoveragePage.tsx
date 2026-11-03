import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import type { Band } from '../rounds/api'
import { bandClass, dateOnly, score, seconds } from '../rounds/feedback/format'
import { getTopicCoverage, layerOf, type CoverageLayer, type HistoryEntry, type ObjectiveCoverage, type TopicDetail } from './coverage'
import { statusClass } from './coverageFormat'
import { progressStrings } from './strings'
import { ContentLanguageProvider, useContentLanguage } from '../language/ContentLanguage'
import { useSubjectLanguage } from '../language/useSubjectLanguage'
import { Loading } from '../../ui/Spinner'

const c = progressStrings.coverage
const t = progressStrings.topic
const dash = progressStrings.dash

function BandPill({ band }: { band: Band }) {
  return <span className={bandClass(band)}>{progressStrings.bandWords[band]}</span>
}

function Deciding({ line }: { line: ObjectiveCoverage }) {
  const found = line.deciding
  if (!found) return <span>{t.notAnswered}</span>
  return (
    <span>
      {line.assisted ? t.answeredAgain : t.latestIndependent} <BandPill band={found.band} /> ({score(found.factual)}){' '}
      {t.on} {dateOnly(found.at)}
    </span>
  )
}

function hint(line: ObjectiveCoverage): string {
  if (line.assisted) return t.hintAssisted
  if (line.status === 'strong') return t.hintStrong
  if (line.status === 'not_practised') return t.hintNone
  return t.hintImproving
}

function Objective({ line }: { line: ObjectiveCoverage }) {
  const cl = useContentLanguage()
  return (
    <li>
      <div>
        {line.text === null ? (
          <p className="title">{c.wholeTopic}</p>
        ) : (
          <p className="title content" lang={cl.bcp47}>
            {cl.typeset(line.text)}
          </p>
        )}
        <div className="meta">
          <span className={`status ${statusClass(line.status)}`}>{c.status[line.status]}</span>
          {line.assisted && <span className="chip">{t.withHelp}</span>}
          <Deciding line={line} />
        </div>
        <p className="note mt-2">{hint(line)}</p>
      </div>
    </li>
  )
}

function Attempt({ entry }: { entry: HistoryEntry }) {
  const label = t.attempt[entry.attempt_kind] ?? entry.attempt_kind
  if (entry.attempt_kind === 'immediate')
    return (
      <span className="line-stack">
        <span>{label}</span>
        <span>
          <span className="chip">{t.withHelp}</span>
        </span>
      </span>
    )
  if (entry.attempt_kind === 'retry')
    return (
      <span className="line-stack">
        <span>{label}</span>
        <span className="note">{t.ownScore}</span>
      </span>
    )
  return <>{label}</>
}

function Result({ entry }: { entry: HistoryEntry }) {
  if (entry.status === 'failed') return <span className="chip error">{t.unresolved}</span>
  if (entry.status === 'processing') return <span className="chip">{t.processing}</span>
  if (entry.status === 'interrupted') return <span className="chip warn">{t.interrupted}</span>
  if (entry.status === 'not_assessed') return <span className="chip">{t.notAssessed}</span>
  return entry.band ? <BandPill band={entry.band} /> : <>{dash}</>
}

function HistoryRow({ entry }: { entry: HistoryEntry }) {
  const cl = useContentLanguage()
  const openable = entry.status === 'ready' || entry.status === 'failed' || entry.status === 'not_assessed'
  const number = (value: number | null) => (value === null ? dash : score(value))
  return (
    <tr>
      <td className="nowrap num">{dateOnly(entry.at)}</td>
      <td className="q-col">
        <p className="content" lang={cl.bcp47}>
          {cl.typeset(entry.question_text)}
        </p>
        <p className="note">{t.difficulty[entry.difficulty] ?? entry.difficulty}</p>
      </td>
      <td>
        <Attempt entry={entry} />
      </td>
      <td className="nowrap">
        <Result entry={entry} />
      </td>
      <td className="r">{number(entry.factual)}</td>
      <td className="r">{number(entry.language)}</td>
      <td className="r">{entry.preparation_used_ms === null ? dash : seconds(entry.preparation_used_ms)}</td>
      <td>
        {openable && (
          <Link className="btn small" to={`/rounds/${entry.round_id}/answers/${entry.attempt_id}`}>
            {t.open}
          </Link>
        )}
      </td>
    </tr>
  )
}

function Detail({ topic, layer = 'all' }: { topic: TopicDetail; layer?: CoverageLayer }) {
  const cl = useContentLanguage()
  const coverage = `/progress/coverage?subject=${topic.subject_id}${layer === 'materials' ? '&layer=materials' : ''}`
  return (
    <>
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label={t.crumbs}>
            <Link to={coverage}>{progressStrings.title}</Link> <span aria-hidden="true">›</span>{' '}
            <Link to={coverage}>
              {plNbsp(topic.subject_name)}
            </Link>{' '}
            <span aria-hidden="true">›</span> <span lang={cl.bcp47}>{cl.typeset(topic.title)}</span>
          </nav>
          <h1 className="content" lang={cl.bcp47}>
            {cl.typeset(topic.title)}
          </h1>
          <p>
            {t.objectives(topic.objectives.length)} · {t.questions(topic.question_count)}
            {topic.terms.length > 0 && (
              <>
                {' '}
                · {t.terms}{' '}
                <span className="content" lang={cl.bcp47}>
                  {topic.terms.join(', ')}
                </span>
              </>
            )}{' '}
            · {t.sources(topic.passage_count)}
          </p>
        </div>
        <div className="actions">
          <Link className="btn primary" to={`/practice?subject=${topic.subject_id}&sources=${layer}`}>
            {c.practise}
          </Link>
        </div>
      </div>

      <section aria-labelledby="h-objectives">
        <h2 id="h-objectives" className="visually-hidden">
          {t.objectivesHeading}
        </h2>
        <ul className="rows">
          {topic.objectives.map((line) => (
            <Objective key={line.objective_id ?? 'topic'} line={line} />
          ))}
        </ul>
      </section>

      <section className="section" aria-labelledby="h-history">
        <h2 id="h-history">{t.historyHeading}</h2>
        {topic.history.length === 0 ? (
          <p className="muted">{t.noHistory}</p>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table wide dense middle">
                <thead>
                  <tr>
                    <th scope="col">{t.columns.date}</th>
                    <th scope="col" className="q-col">
                      {t.columns.question}
                    </th>
                    <th scope="col">{t.columns.attempt}</th>
                    <th scope="col">{t.columns.result}</th>
                    <th scope="col" className="r">
                      {t.columns.factual}
                    </th>
                    <th scope="col" className="r">
                      {t.columns.language}
                    </th>
                    <th scope="col" className="r">
                      {t.columns.prep}
                    </th>
                    <th scope="col" aria-label={t.columns.open}></th>
                  </tr>
                </thead>
                <tbody>
                  {topic.history.map((entry) => (
                    <HistoryRow key={entry.attempt_id} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
            {topic.history.some((entry) => entry.attempt_kind === 'retry') && (
              <p className="note mt-3 measure-text">{t.retryNote}</p>
            )}
          </>
        )}
      </section>

      {topic.sources.length > 0 && (
        <section className="box flow section" aria-labelledby="h-sources">
          <h2 id="h-sources">{t.sourcesHeading}</h2>
          {topic.sources.map((source) => (
            <blockquote className="source-quote" key={source.passage_id}>
              <span className="source">
                <span className="file-name">{source.document_title}</span> · {t.version(source.document_version)} ·{' '}
                {source.locator}
              </span>
              <p lang={cl.bcp47}>{cl.typeset(source.excerpt)}</p>
            </blockquote>
          ))}
          <p className="small">
            <Link to={`/library/${topic.subject_id}`}>{t.openDocument}</Link>
          </p>
        </section>
      )}
    </>
  )
}

/** One topic: its objectives with the evidence behind each status, the answer history and the sources. */
export function TopicCoveragePage() {
  const topicId = Number(useParams().topicId)
  const [params] = useSearchParams()
  const layer = layerOf(params.get('layer'))
  const [topic, setTopic] = useState<TopicDetail | null>(null)
  const [error, setError] = useState<{ id: number; message: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    getTopicCoverage(topicId, layer)
      .then((found) => {
        if (cancelled) return
        setTopic(found)
        setError(null)
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError({ id: topicId, message: failure instanceof Error ? failure.message : t.loadError })
      })
    return () => {
      cancelled = true
    }
  }, [topicId, layer])

  const current = topic && topic.topic_id === topicId ? topic : null
  const failed = error && error.id === topicId ? error.message : null
  // the coverage API has no language: ask for the subject
  const language = useSubjectLanguage(current?.subject_id)

  if (failed)
    return (
      <Notice kind="error">
        <p>{failed}</p>
        <p>
          <Link to="/progress/coverage">{t.back}</Link>
        </p>
      </Notice>
    )
  if (!current) return <Loading>{t.loading}</Loading>
  return (
    <ContentLanguageProvider code={language}>
      <Detail topic={current} layer={layer} />
    </ContentLanguageProvider>
  )
}
