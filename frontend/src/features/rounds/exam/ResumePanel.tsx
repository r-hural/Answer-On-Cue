import { Link } from 'react-router-dom'
import { plNbsp } from '../../../i18n/polish'
import type { RoundState } from '../api'
import { questionSize } from '../clock'
import { dateTime } from '../feedback/format'
import { roundTypeLabel } from '../roundType'
import { roundStrings } from '../strings'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.recovery
const exam = roundStrings.exam

/** Zero-based indexes grouped by what happened to them, for the panel's list. */
function groups(round: RoundState) {
  const answered: number[] = []
  const assessing: number[] = []
  let interrupted: number | null = null
  const notAsked: number[] = []
  round.items.forEach((item, index) => {
    const latest = item.latest_attempt
    const current = index === round.current_index
    if (current && latest?.outcome === 'interrupted') interrupted = index
    else if (index < round.current_index || (current && latest !== null && latest.outcome !== 'pending')) {
      answered.push(index)
      if (latest?.processing_status === 'processing') assessing.push(index)
    } else notAsked.push(index)
  })
  return { answered, assessing, interrupted: interrupted as number | null, notAsked }
}

/** "Continue your round?" (exam--resume.html): a paused round opened after a reload, from Home or in another tab. */
export function ResumePanel({
  round,
  subject,
  disabled,
  onEnd,
}: {
  round: RoundState
  subject: string | null
  disabled: boolean
  onEnd: () => void
}) {
  const cl = useContentLanguage()
  const { answered, assessing, interrupted, notAsked } = groups(round)
  const n = round.current_index + 1
  const pausedAt = round.clock?.paused_at ?? null
  const timing = round.config.feedback_timing === 'exam' ? exam.feedbackExam : exam.feedbackPractice
  const question = round.current
  return (
    <section className="panel flow" aria-labelledby="resume-title">
      <h2 id="resume-title">{t.title}</h2>
      <p className="lead">
        {subject !== null && (
          <>
            <b className="content">
              {plNbsp(subject)}
            </b>{' '}
            ·{' '}
          </>
        )}
        {roundTypeLabel(round.config.round_type)} · {timing} · {t.stopped(pausedAt === null ? null : dateTime(pausedAt), n, interrupted !== null)}
      </p>
      <ul className="list">
        {answered.length > 0 && <li>{t.answered(answered, assessing)}</li>}
        {interrupted !== null && <li>{t.interrupted(interrupted + 1)}</li>}
        {notAsked.length > 0 && <li>{t.notAsked(notAsked)}</li>}
      </ul>
      {question && (
        <>
          <h3>{t.question(n)}</h3>
          <p className="content-q" data-size={questionSize(question.text)} lang={cl.bcp47}>
            {cl.typeset(question.text)}
          </p>
        </>
      )}
      <div className="actions static">
        <Link className="btn primary big" to={`/practice/${round.id}/microphone?resume=1`}>
          {t.checkAndContinue}
        </Link>
        <button type="button" className="btn" onClick={onEnd} disabled={disabled}>
          {t.end}
        </button>
        <Link className="btn" to="/">
          {t.notNow}
        </Link>
      </div>
    </section>
  )
}
