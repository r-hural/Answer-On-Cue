import { Link } from 'react-router-dom'
import type { FollowUpBrief } from '../reports'
import { roundStrings } from '../strings'
import { bandClass, score } from './format'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review.followUp
const bandWords = roundStrings.exam.feedback.bands

/** The main answer's follow-up: its own result and a link, or why there was none. Never this question's score. */
export function FollowUpBox({ brief, roundId }: { brief: FollowUpBrief; roundId: number }) {
  const cl = useContentLanguage()
  return (
    <section className="box tint flow" aria-labelledby="h-followup">
      <h2 id="h-followup">{t.title}</h2>
      {brief.asked ? (
        <>
          {brief.question_text && (
            <p className="content" lang={cl.bcp47}>
              {cl.quote(cl.typeset(brief.question_text))}
            </p>
          )}
          {brief.status === 'failed' ? (
            <p>
              <span className="chip error">{roundStrings.review.unresolved}</span> <span className="small muted">{t.unresolved}</span>
            </p>
          ) : brief.status === 'processing' ? (
            <p className="small muted">{t.processing}</p>
          ) : (
            brief.band && (
              <p>
                <span className={bandClass(brief.band)}>{bandWords[brief.band]}</span>{' '}
                {brief.factual !== null && <span className="small muted num">{t.factual(score(brief.factual))}</span>}
              </p>
            )
          )}
          {brief.note && brief.status === 'ready' && (
            <p className="content small" lang={cl.bcp47}>
              {cl.typeset(brief.note)}
            </p>
          )}
          {brief.attempt_id !== null && (
            <p className="small">
              <Link to={`/rounds/${roundId}/answers/${brief.attempt_id}`}>{t.full}</Link>
            </p>
          )}
          <p className="small muted">{t.apart}</p>
        </>
      ) : (
        <p className="small">
          {brief.reason ? t.reasons[brief.reason] : t.reasons.not_reached}
          {brief.reason === 'generation_failed' && brief.failure_message ? ` ${brief.failure_message}` : ''}
        </p>
      )}
    </section>
  )
}
