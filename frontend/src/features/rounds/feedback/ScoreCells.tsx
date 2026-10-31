import { roundStrings } from '../strings'
import type { AttemptReport } from '../reports'
import { bandClass, metricOrUnavailable, score, seconds, seconds1 } from './format'

const t = roundStrings.review
const bands = roundStrings.exam.feedback.bands

/** The four cells under the question: factual result, knowledge and language, speed, fluency. */
export function ScoreCells({ report }: { report: AttemptReport }) {
  const { result } = report
  const assessment = result.assessment
  const scores = assessment?.scores ?? null
  const band = assessment?.band ?? null
  if (!assessment || !scores || !band) return null

  const noAnswer = assessment.status === 'no_answer'
  const metrics = result.metrics
  const ai = assessment.ai
  const languageShare = 100 - assessment.factual_weights[0] - assessment.factual_weights[1]

  const factualNote = noAnswer
    ? result.outcome === 'skipped'
      ? t.skipped(Math.round(scores.factual))
      : t.chose(Math.round(scores.factual))
    : t.factualNote(Math.round(scores.factual), Math.round(scores.correctness), Math.round(scores.completeness))

  const languageNote = noAnswer
    ? t.languageNoAnswer
    : scores.language === null
      ? t.languageNone
      : t.languageNote(Math.round(scores.language), languageShare)

  let speed: { value: string; note: string }
  if (noAnswer) speed = { value: '—', note: t.speedNoAnswer }
  else if (!metrics) speed = { value: metricOrUnavailable(null), note: t.prepUsed }
  else {
    const latency = metrics.speech_start_latency_ms
    const start =
      latency === 0
        ? t.startedAtOnce
        : latency === null
          ? `${t.startOfSpeech}: ${metricOrUnavailable(null, metrics.unavailable.speech_start_latency_ms)}`
          : t.speechStartedAfter(seconds1(latency))
    speed = { value: seconds(metrics.preparation_used_ms), note: `${t.prepUsed} · ${start}` }
  }

  const notes: string[] = []
  if (metrics?.long_pause_count != null) notes.push(t.pauseCount(metrics.long_pause_count))
  if (metrics?.filler_count != null && metrics.filler_count > 0) notes.push(t.fillersAtLeast(metrics.filler_count))

  return (
    <div className="scores" aria-label={t.scores}>
      <div>
        <div className="k">{t.factual}</div>
        <span className={bandClass(band)}>{bands[band]}</span>
        <p className="note num">{factualNote}</p>
      </div>
      <div>
        <div className="k">{t.knowledge}</div>
        <div className="v">{score(scores.total)}</div>
        <p className="note num">{languageNote}</p>
      </div>
      <div>
        <div className="k">{t.speed}</div>
        <div className="v measure">{speed.value}</div>
        <p className="note">{speed.note}</p>
      </div>
      <div>
        <div className="k">{t.fluency}</div>
        {noAnswer ? (
          <>
            <div className="v">—</div>
            <p className="note">{t.fluencyNoAnswer}</p>
          </>
        ) : ai?.fluency == null ? (
          <>
            <div className="v measure">{t.fluencyUnreliable}</div>
            {notes.length > 0 && <p className="note">{notes.join(' · ')}</p>}
          </>
        ) : (
          <>
            <div className="v">
              {ai.fluency} <small>{t.approximate}</small>
            </div>
            {notes.length > 0 && <p className="note">{notes.join(' · ')}</p>}
          </>
        )}
      </div>
    </div>
  )
}
