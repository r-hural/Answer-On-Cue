import { Link } from 'react-router-dom'
import { bandClass, score, seconds } from '../feedback/format'
import type { AnswerBrief, RetryComparison, RoundReport } from '../reports'
import { roundStrings } from '../strings'
import { useContentLanguage } from '../../language/ContentLanguage'

const r = roundStrings.repetition
const bandWords = roundStrings.exam.feedback.bands

const signed = (value: number) => `${value >= 0 ? '+' : '−'}${score(Math.abs(value))}`
const NO_ANSWER = ['dont_know', 'skipped']

/** One side of a comparison row: its band and factual score, or why there is none. */
function Side({ brief }: { brief: AnswerBrief }) {
  return (
    <>
      {brief.band && <span className={bandClass(brief.band)}>{bandWords[brief.band]}</span>}
      <span>{NO_ANSWER.includes(brief.outcome) || brief.factual === null ? (r.noAnswer[brief.outcome] ?? '—') : score(brief.factual)}</span>
    </>
  )
}

/** The retry round's score next to the same questions in the original round (summary--retry-round.html). */
function ScorePanel({ report, comparison }: { report: RoundReport; comparison: RetryComparison }) {
  const own = report.knowledge?.primary_score ?? null
  const before = comparison.original_mean_total
  const after = comparison.retry_mean_total
  const factualBefore = comparison.original_mean_factual
  const factualAfter = comparison.retry_mean_factual
  return (
    <section className="panel flow" aria-labelledby="h-score">
      <h2 id="h-score">{r.scoreTitle}</h2>
      <div className="scores">
        <div>
          <div className="k">{r.thisRetry}</div>
          <div className="v num">{own === null ? '—' : score(own)}</div>
          <p className="note">{r.thisRetryNote(report.asked)}</p>
        </div>
        <div>
          <div className="k">{r.sameOriginal}</div>
          <div className="v num">{before === null ? '—' : score(before)}</div>
          <p className="note">{r.sameOriginalNote}</p>
        </div>
        <div>
          <div className="k">{r.improvement}</div>
          <div className="v num">{before === null || after === null ? '—' : signed(after - before)}</div>
          {factualBefore !== null && factualAfter !== null && (
            <p className="note num">{r.factualChange(score(factualBefore), score(factualAfter), signed(factualAfter - factualBefore))}</p>
          )}
        </div>
      </div>
      <p className="small muted">{r.scoreNote(comparison.rows.length)}</p>
    </section>
  )
}

/** The retry round's own sections: its score, original and retry per question, what is still below Good, speed. */
export function RetrySummary({ report, primary }: { report: RoundReport; primary: boolean }) {
  const cl = useContentLanguage()
  const comparison = report.retry_comparison
  if (!comparison) return null
  const below = comparison.rows.filter((row) => comparison.still_below.includes(row.ordinal))
  const sources = `&sources=${report.config.source_policy === 'materials' ? 'materials' : 'all'}`
  const prepBefore = comparison.original_median_preparation_ms
  const prepAfter = comparison.retry_median_preparation_ms
  const direction = prepBefore === null || prepAfter === null ? null : prepAfter < prepBefore ? 'down' : prepAfter > prepBefore ? 'up' : 'same'
  return (
    <>
      <ScorePanel report={report} comparison={comparison} />

      <section className="section" aria-labelledby="h-compare">
        <h2 id="h-compare">{r.compareRows}</h2>
        <ul className="rows stack-sm">
          {comparison.rows.map((row) => (
            <li key={row.item_id}>
              <div>
                <p className="q-line" lang={cl.bcp47}>
                  <b lang="en">{row.ordinal}</b>
                  {cl.typeset(row.question_text)}
                </p>
                <div className="meta num">
                  <span>{r.original}</span>
                  <Side brief={row.original} />
                  <span>{r.toRetry}</span>
                  <Side brief={row.again} />
                </div>
              </div>
              {row.factual_change !== null && (
                <div className="end">
                  <span className="num">
                    <strong>{signed(row.factual_change)}</strong> <span className="muted">{r.factualWord}</span>
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
        <p className="note mt-4">{r.compareNoteRows}</p>
      </section>

      <div className="cols-2 section">
        <section className="panel flow" aria-labelledby="h-still">
          <h2 id="h-still">{r.stillBelow}</h2>
          {below.length > 0 ? (
            <>
              <ul className="list" lang={cl.bcp47}>
                {below.map((row) => (
                  <li key={row.item_id} className="content">
                    {cl.typeset(row.question_text)}
                  </li>
                ))}
              </ul>
              <p className="small muted">{r.noChain}</p>
              <div className="actions">
                <Link className={primary ? 'btn primary' : 'btn'} to={`/practice?subject=${report.subject_id}${sources}`}>
                  {r.newOnTopics}
                </Link>
              </div>
            </>
          ) : (
            <p>{r.allGood}</p>
          )}
        </section>

        <section className="panel flow" aria-labelledby="h-retry-speed">
          <h2 id="h-retry-speed">{r.speedTitle}</h2>
          <dl className="facts">
            <dt>{r.prepMedian}</dt>
            <dd className="num">
              {prepAfter === null ? '—' : seconds(prepAfter)}
              {direction !== null && prepBefore !== null && (
                <>
                  {' '}
                  <small>{r.prepFrom(seconds(prepBefore), direction)}</small>
                </>
              )}
            </dd>
          </dl>
          <p className="small muted">{r.speedApart}</p>
        </section>
      </div>
    </>
  )
}
