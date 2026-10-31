import { formatClock } from '../clock'
import { roundStrings } from '../strings'

const t = roundStrings.exam.pause

export function PausePanel({
  interrupted,
  index,
  total,
  doneCount,
  pausedMs,
  pauseCount,
  recordedMs,
  disabled,
  onResume,
  onEnd,
}: {
  interrupted: boolean
  /** Zero-based index of the current question. */
  index: number
  total: number
  doneCount: number
  pausedMs: number
  pauseCount: number
  /** Length of the answer kept by this pause, when this page recorded it. */
  recordedMs: number | null
  disabled: boolean
  onResume: () => void
  onEnd: () => void
}) {
  const n = index + 1
  const clock = formatClock(pausedMs)
  const kept = interrupted && recordedMs !== null && recordedMs > 0
  return (
    <section className="panel flow" aria-labelledby="pause-title">
      <h2 id="pause-title">{interrupted ? t.titleDuring(n) : t.title}</h2>
      {interrupted ? (
        <p className="lead">
          {t.leadInterrupted}
          {kept && <> {t.keptNotScored(formatClock(recordedMs))}</>}
        </p>
      ) : (
        <p className="lead">{t.lead(doneCount, total)}</p>
      )}
      {kept && (
        <div className="readout" role="img" aria-label={t.recordedAria(formatClock(recordedMs))}>
          <span className="k">{t.recorded}</span>
          <b>{formatClock(recordedMs)}</b>
          <span className="note">{t.recordedNote}</span>
        </div>
      )}
      <div className="readout" role="img" aria-label={t.pausedAria(clock, pauseCount)}>
        <span className="k">{t.pausedFor}</span>
        <b>{clock}</b>
        <span className="note">{t.pauseNumber(pauseCount)}</span>
      </div>
      {interrupted && <p className="lead">{t.restartLead(n)}</p>}
      <div className="actions static">
        <button type="button" className="btn primary big" onClick={onResume} disabled={disabled}>
          {interrupted ? t.restart(n) : t.resume(n)} <kbd>P</kbd>
        </button>
        <button type="button" className="btn" onClick={onEnd} disabled={disabled}>
          {t.end}
        </button>
      </div>
    </section>
  )
}
