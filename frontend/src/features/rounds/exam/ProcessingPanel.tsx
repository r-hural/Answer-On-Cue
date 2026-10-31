import type { AttemptResult, FollowUpState } from '../api'
import { formatClock } from '../clock'
import { roundStrings } from '../strings'
import { TimerRing } from './TimerRing'

const t = roundStrings.exam.processing
const f = roundStrings.exam.followUp

type StepState = 'done' | 'progress' | 'waiting' | 'failed'

const CHIP: Record<StepState, { className: string; label: string }> = {
  done: { className: 'chip ok', label: t.done },
  progress: { className: 'chip accent', label: t.inProgress },
  waiting: { className: 'chip', label: t.waiting },
  failed: { className: 'chip warn', label: f.failedChip },
}

function followUpStep(followUp: FollowUpState | null): StepState {
  switch (followUp?.status) {
    case 'generating':
      return 'progress'
    case 'ready':
      return 'done'
    case 'failed':
    case 'omitted':
      return 'failed'
    default:
      return 'waiting'
  }
}

function stepsOf(stage: AttemptResult['stage'] | undefined): [StepState, StepState, StepState] {
  switch (stage) {
    case 'saved':
      return ['done', 'waiting', 'waiting']
    case 'analysing':
    case 'transcribing':
      return ['done', 'progress', 'waiting']
    case 'assessing':
      return ['done', 'done', 'progress']
    case 'done':
    case 'not_assessed':
      return ['done', 'done', 'done']
    default:
      return ['progress', 'waiting', 'waiting']
  }
}

function recordedClock(result: AttemptResult | null): string | null {
  return result?.recording ? formatClock(result.recording.duration_ms) : null
}

/** The ring of the processing screen: the recorded length, the microphone is off. */
export function ProcessingTimer({ result }: { result: AttemptResult | null }) {
  const clock = recordedClock(result)
  return (
    <TimerRing
      status={t.status}
      statusKind="listen"
      fraction={1}
      clock={clock ?? '…'}
      sub={t.recorded}
      ariaLabel={t.ringLabel(clock)}
      prep
      role="img"
      note={<small className="muted">{t.micOff}</small>}
    />
  )
}

export function ProcessingPanel({
  result,
  followUp,
  exam = false,
}: {
  result: AttemptResult | null
  /** The main answer's follow-up step; undefined when no follow-up comes after this answer. */
  followUp?: FollowUpState | null
  exam?: boolean
}) {
  const withFollowUp = followUp !== undefined
  const steps: StepState[] = stepsOf(result?.stage)
  const labels = [t.saved, t.transcribing, t.assessing]
  if (withFollowUp) {
    steps.push(followUpStep(followUp))
    labels.push(f.preparing)
  }
  return (
    <div className="exam-help">
      <div className="flow">
        <h2>{t.title}</h2>
        <p>{t.lead(recordedClock(result))}</p>
        <ul className="rows compact" aria-label={t.stepsLabel}>
          {labels.map((label, i) => (
            <li key={label}>
              <span>{label}</span>
              <span className={CHIP[steps[i]].className}>{CHIP[steps[i]].label}</span>
            </li>
          ))}
        </ul>
        {withFollowUp && <p className="note">{exam ? f.nextExam : f.next}</p>}
      </div>
    </div>
  )
}
