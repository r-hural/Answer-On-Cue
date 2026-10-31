import { plNbsp } from '../../../i18n/polish'
import { appStrings } from '../../../i18n/app'
import type { FeedbackTiming, RoundType } from '../api'
import { roundTypeLabel } from '../roundType'
import { roundStrings } from '../strings'

const t = roundStrings.exam

export function ExamHeader({
  subject,
  timing,
  roundType,
  onPause,
  pauseDisabled,
}: {
  subject: string | null
  timing: FeedbackTiming
  roundType?: RoundType
  onPause: (() => void) | null
  pauseDisabled: boolean
}) {
  return (
    <header className="shell focus">
      <div className="shell-in">
        <span className="brand">{appStrings.appName}</span>
        <p className="shell-ctx">
          {subject && <b>{plNbsp(subject)}</b>} · {roundTypeLabel(roundType)} ·{' '}
          {timing === 'exam' ? t.feedbackExam : t.feedbackPractice}
        </p>
        {onPause && (
          <button type="button" className="btn" onClick={onPause} disabled={pauseDisabled}>
            {t.pauseRound} <kbd>{t.keys.p}</kbd>
          </button>
        )}
      </div>
    </header>
  )
}
