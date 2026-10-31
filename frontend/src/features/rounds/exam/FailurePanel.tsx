import { svgProps } from '../../../ui/Notice'
import { recordingAudioUrl } from '../../recording/api'
import type { AttemptResult } from '../api'
import { formatClock } from '../clock'
import { roundStrings } from '../strings'
import { TimerRing } from './TimerRing'

const t = roundStrings.exam.failure

function recordedClock(result: AttemptResult): string | null {
  return result.recording ? formatClock(result.recording.duration_ms) : null
}

/** The ring of a failed answer: the recording is kept, nothing was scored. */
export function FailureTimer({ result }: { result: AttemptResult }) {
  const clock = recordedClock(result)
  return (
    <TimerRing
      status={t.status}
      statusKind="listen"
      fraction={1}
      clock={clock ?? '—'}
      sub={t.recordedSub}
      ariaLabel={t.ringLabel(clock)}
      prep
      role="img"
      note={<small className="muted">{t.kept}</small>}
    />
  )
}

/** A technical failure is never a score: no band, no points, only what happened and what to do. */
export function FailureNotice({ result, note = null }: { result: AttemptResult; note?: string | null }) {
  const failure = result.failure
  return (
    <div className="notice warn" role="status">
      <svg {...svgProps}>
        <circle cx="10" cy="10" r="7.5" />
        <path d="M10 6v4.5" />
        <path d="M10 13.6v.1" />
      </svg>
      <div className="flow">
        <p>
          <strong>{t.title}</strong> {failure?.message} {t.technical}
          {result.recording ? ` ${t.safe}` : ''}
        </p>
        {result.recording && (
          <audio controls src={recordingAudioUrl(result.recording.id)} aria-label={roundStrings.exam.feedback.player} />
        )}
        {note && <p>{note}</p>}
        <p>{t.unresolved}</p>
      </div>
    </div>
  )
}
