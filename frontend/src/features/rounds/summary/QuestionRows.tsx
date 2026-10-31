import { Link } from 'react-router-dom'
import { GIVEN_UP } from '../api'
import { GiveUpControl } from '../feedback/GiveUpControl'
import { bandClass, metricOrUnavailable, score, seconds, seconds1 } from '../feedback/format'
import type { ItemRow } from '../reports'
import { roundStrings } from '../strings'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.summary
const r = roundStrings.repetition
const bandWords = roundStrings.exam.feedback.bands

const NO_ANSWER_CHIP: Record<string, string> = { dont_know: t.dontKnow, skipped: t.skipped }

function Meta({ item }: { item: ItemRow }) {
  if (item.status === 'not_asked') {
    return (
      <div className="meta">
        <span className="chip">{t.notAsked}</span>
      </div>
    )
  }
  if (item.status === 'processing') {
    return (
      <div className="meta">
        <span className="chip accent">{t.processing}</span>
        <span>{t.processingNote}</span>
      </div>
    )
  }
  if (item.status === 'failed') {
    return (
      <div className="meta">
        <span className="chip error">{t.unresolved}</span>
        <span>{item.failure?.message}</span>
      </div>
    )
  }
  if (item.outcome === GIVEN_UP) {
    return (
      <div className="meta">
        <span className="chip">{t.notAssessed}</span>
        <span>{t.notAssessedNote}</span>
      </div>
    )
  }
  // an answer that was not given has nothing to measure; a missing measurement of a real one says so
  const noAnswer = item.outcome in NO_ANSWER_CHIP
  const missing = (fn: () => string) => (noAnswer ? t.dash : fn())
  return (
    <div className="meta num">
      {item.band ? (
        <span className={bandClass(item.band)}>{bandWords[item.band]}</span>
      ) : (
        <>
          <span className="chip warn">{t.noSpeech}</span>
          <span>{t.notScored}</span>
        </>
      )}
      {noAnswer && <span className="chip">{NO_ANSWER_CHIP[item.outcome]}</span>}
      {item.again && (
        <>
          <span>{r.answeredAgain}</span>
          {item.again.band ? (
            <span className={bandClass(item.again.band)}>{bandWords[item.again.band]}</span>
          ) : (
            <span>{r.noAnswer[item.again.outcome] ?? t.dash}</span>
          )}
          <span className="chip">{r.withHelp}</span>
        </>
      )}
      {item.restarted && <span className="chip">{t.restarted}</span>}
      {item.band && (
        <>
          <span>
            {t.factualShort} {item.factual === null ? metricOrUnavailable(null) : score(item.factual)}
          </span>
          <span>
            {t.prepUsed} {missing(() => metricOrUnavailable(item.preparation_used_ms, undefined, seconds))}
          </span>
          <span>
            {t.startOfSpeech} {missing(() => metricOrUnavailable(item.speech_start_latency_ms, undefined, seconds1))}
          </span>
          <span>
            {t.fluencyShort} {missing(() => metricOrUnavailable(item.fluency, undefined, score))}
          </span>
        </>
      )}
    </div>
  )
}

/** One row per main question: the band and measurements, a link to its feedback, or what to do about it. */
export function QuestionRows({
  roundId,
  items,
  retryingId,
  onRetry,
  onGiveUp,
  givingUpId = null,
}: {
  roundId: number
  items: ItemRow[]
  retryingId: number | null
  onRetry: (attemptId: number) => void
  /** "Give up this answer" on failed rows; absent for open rounds and withheld results. */
  onGiveUp?: (attemptId: number) => void
  givingUpId?: number | null
}) {
  const cl = useContentLanguage()
  // one primary button per screen: the first unresolved answer that can be retried
  const firstRetry = items.find((item) => item.status === 'failed' && item.failure?.retryable && item.attempt_id !== null)
  return (
    <ul className="rows stack-sm">
      {items.map((item) => {
        const failed = item.status === 'failed' && item.attempt_id !== null
        const retry = failed && item.failure?.retryable
        const busy = retryingId !== null || givingUpId !== null
        return (
          <li key={item.item_id}>
            <div>
              <p className="q-line" lang={cl.bcp47}>
                <b lang="en">{item.ordinal}</b>
                {cl.typeset(item.question_text)}
              </p>
              <Meta item={item} />
            </div>
            {item.status === 'ready' && item.attempt_id !== null && (
              <Link className="btn small" to={`/rounds/${roundId}/answers/${item.attempt_id}`}>
                {t.feedback}
              </Link>
            )}
            {failed && (retry || onGiveUp) && (
              <div className="end">
                {retry && (
                  <button
                    type="button"
                    className={item === firstRetry ? 'btn small primary' : 'btn small'}
                    disabled={busy}
                    onClick={() => onRetry(item.attempt_id as number)}
                  >
                    {retryingId === item.attempt_id ? t.retrying : t.retry}
                  </button>
                )}
                {onGiveUp && (
                  <GiveUpControl
                    onGiveUp={() => onGiveUp(item.attempt_id as number)}
                    busy={givingUpId === item.attempt_id}
                    disabled={busy}
                  />
                )}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
