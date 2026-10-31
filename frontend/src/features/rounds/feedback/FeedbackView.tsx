import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Notice } from '../../../ui/Notice'
import { recordingAudioUrl } from '../../recording/api'
import { formatClock, questionSize } from '../clock'
import type { AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { AnswerPanel } from './AnswerPanel'
import { AudioPlayer, useAudioPlayer } from './AudioPlayer'
import { BetterPanel, ModelAnswerPanel } from './BetterPanel'
import { CompareSection } from './CompareSection'
import { LanguagePanel } from './LanguagePanel'
import { PointsPanel } from './PointsPanel'
import { FollowUpBox } from './FollowUpBox'
import { GiveUpControl } from './GiveUpControl'
import { ScoreCells } from './ScoreCells'
import { SourcesBox } from './SourcesBox'
import { useContentLanguage } from '../../language/ContentLanguage'
import { GIVEN_UP } from '../api'

const t = roundStrings.review
const r = roundStrings.repetition

export interface FeedbackActions {
  /** "Try again" after a technical failure; absent when the server says it cannot be retried. */
  onRetry?: (() => void) | null
  retrying?: boolean
  /** "Send the answer again": the recording is still held by this page's upload queue. */
  onResend?: (() => void) | null
  /** "Give up this answer": only for a failed answer of an ended round. */
  onGiveUp?: (() => void) | null
  givingUp?: boolean
}

function FailureView({ report, actions }: { report: AttemptReport; actions: FeedbackActions }) {
  const { result } = report
  const player = useAudioPlayer(result.recording?.duration_ms ?? 0)
  const failure = result.failure
  return (
    <Notice kind="warn">
      <div className="flow">
        <p>
          <span className="chip error">{t.unresolved}</span> <strong>{t.failureTitle}</strong> {failure?.message} {t.failureTechnical}
          {result.recording ? ` ${t.failureSafe}` : ''}
        </p>
        {result.recording && (
          <AudioPlayer
            player={player}
            src={recordingAudioUrl(result.recording.id)}
            label={t.playerLabel(formatClock(result.recording.duration_ms))}
          />
        )}
        <p>{t.failureNote}</p>
        {(actions.onRetry || actions.onResend || actions.onGiveUp) && (
          <div className="actions">
            {actions.onResend && (
              <button type="button" className="btn small primary" onClick={actions.onResend} disabled={actions.retrying}>
                {t.resend}
              </button>
            )}
            {actions.onRetry && (
              <button
                type="button"
                className={actions.onResend ? 'btn small' : 'btn small primary'}
                onClick={actions.onRetry}
                disabled={actions.retrying}
              >
                {actions.retrying ? t.retrying : t.retry}
              </button>
            )}
            {actions.onGiveUp && (
              <GiveUpControl onGiveUp={actions.onGiveUp} busy={actions.givingUp} disabled={actions.retrying} />
            )}
          </div>
        )}
      </div>
    </Notice>
  )
}

/** A given-up answer: not assessed, so no score or panel of any kind; the recording still plays. */
function NotAssessedView({ report }: { report: AttemptReport }) {
  const { result } = report
  const player = useAudioPlayer(result.recording?.duration_ms ?? 0)
  return (
    <Notice kind="info">
      <div className="flow">
        <p>
          <span className="chip">{roundStrings.summary.notAssessed}</span> <strong>{t.notAssessedTitle}</strong>{' '}
          {t.notAssessedText}
        </p>
        {result.recording && (
          <AudioPlayer
            player={player}
            src={recordingAudioUrl(result.recording.id)}
            label={t.playerLabel(formatClock(result.recording.duration_ms))}
          />
        )}
      </div>
    </Notice>
  )
}

/**
 * The feedback of one answer, from the question card down: scores, points, the answer with playback,
 * a better version and the model answer by the band rule, language, speed and sources.
 * A technical failure shows no score of any kind.
 */
export function FeedbackView({
  report,
  actions = {},
  again,
  children,
}: {
  report: AttemptReport
  actions?: FeedbackActions
  /** The "Answer again" box, placed before the follow-up box (practice feedback only). */
  again?: ReactNode
  /** The screen's own buttons (Next question, back to the summary). */
  children?: ReactNode
}) {
  const cl = useContentLanguage()
  const { result } = report
  const assessment = result.assessment
  const player = useAudioPlayer(result.recording?.duration_ms ?? 0)
  const failed = result.status === 'failed'
  const givenUp = result.outcome === GIVEN_UP
  const noSpeech = assessment?.status === 'no_speech'
  const scored = !failed && !givenUp && assessment !== null && assessment.scores !== null && assessment.band !== null
  const auto = report.model_answer_display === 'auto'

  return (
    <>
      <section className="panel q-card" aria-label={t.question}>
        <p className="content-q" data-size={questionSize(report.question_text)} lang={cl.bcp47}>
          {cl.typeset(report.question_text)}
        </p>
      </section>

      {failed && <FailureView report={report} actions={actions} />}

      {givenUp && <NotAssessedView report={report} />}

      {noSpeech && (
        <Notice kind="warn">
          <div className="flow">
            <p>{t.noSpeech}</p>
          </div>
        </Notice>
      )}

      {scored && <ScoreCells report={report} />}

      <CompareSection report={report} />
      {report.again_attempt_id != null && (
        <p className="small">
          <Link to={`/rounds/${report.round_id}/answers/${report.again_attempt_id}`}>{r.secondLink}</Link>
        </p>
      )}

      {!failed && !givenUp && (
        <div className="cols-main">
          <div className="stack">
            {auto && <ModelAnswerPanel report={report} />}
            <PointsPanel report={report} />
            <AnswerPanel report={report} player={player} />
            <BetterPanel report={report} />
            <LanguagePanel report={report} />
          </div>
          <SourcesBox
            sources={report.sources}
            before={
              again || report.follow_up ? (
                <>
                  {again}
                  {report.follow_up && <FollowUpBox brief={report.follow_up} roundId={report.round_id} />}
                </>
              ) : undefined
            }
          />
        </div>
      )}

      {children}
    </>
  )
}
