import { Fragment } from 'react'
import { formatClock } from '../clock'
import type { AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { metricOrUnavailable, pauseSeconds, position, seconds, seconds1 } from './format'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review

/** Speed and fluency as facts; a measurement that is missing says so and why, never a zero. */
export function SpeedPanel({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const { result } = report
  const metrics = result.metrics
  const ai = result.assessment?.ai
  if (!metrics) return null
  const why = (name: string) => metrics.unavailable[name]
  const longest = metrics.pauses.length > 0 ? metrics.pauses.reduce((a, b) => (b.end_ms - b.start_ms > a.end_ms - a.start_ms ? b : a)) : null

  const pauses =
    metrics.long_pause_count === null
      ? metricOrUnavailable(null, why('long_pause_count'))
      : metrics.long_pause_count === 0
        ? t.noPauses
        : [
            t.pausesText(
              metrics.long_pause_count,
              longest ? pauseSeconds(longest.end_ms - longest.start_ms) : metricOrUnavailable(metrics.longest_internal_silence_ms, undefined, pauseSeconds),
              longest ? position(longest.start_ms) : '—',
            ),
            metrics.silence_ratio !== null ? t.silenceShare(Math.round(metrics.silence_ratio * 100)) : null,
          ]
            .filter(Boolean)
            .join(' · ')

  const start =
    metrics.speech_start_latency_ms === 0
      ? t.atOnce
      : metricOrUnavailable(metrics.speech_start_latency_ms, why('speech_start_latency_ms'), seconds1)

  const length =
    metrics.first_to_last_speech_ms !== null && metrics.leading_silence_ms !== null
      ? `${t.recorded(formatClock(metrics.answer_duration_ms))} · ${t.speechFromTo(
          position(metrics.leading_silence_ms),
          position(metrics.leading_silence_ms + metrics.first_to_last_speech_ms),
        )}`
      : t.recorded(formatClock(metrics.answer_duration_ms))

  const rows: [string, string][] = [
    [t.prepUsedRow, seconds(metrics.preparation_used_ms)],
    [t.startOfSpeech, start],
    [t.answerLength, length],
    [t.pauses, pauses],
    [t.pace, metricOrUnavailable(metrics.words_per_minute, why('words_per_minute'), t.wordsPerMinute)],
  ]

  return (
    <>
      <h2>{t.speedFluency}</h2>
      <dl className="facts">
        {rows.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
        <dt>{t.hesitations}</dt>
        <dd>
          {metrics.filler_count === null ? (
            metricOrUnavailable(null, why('filler_count'))
          ) : (
            <>
              {t.fillerText(metrics.filler_count)}
              {metrics.filler_examples.length > 0 && (
                <>
                  {' '}
                  (
                  <span lang={cl.bcp47}>
                    {metrics.filler_examples.map((example) => cl.quote(example)).join(', ')}
                  </span>
                  )
                </>
              )}
              {metrics.fillers_lower_bound && <small>{t.fillerNote}</small>}
            </>
          )}
        </dd>
      </dl>
      {ai?.fluency_feedback && (
        <p className="content reading" lang={cl.bcp47}>
          {cl.typeset(ai.fluency_feedback)}
        </p>
      )}
    </>
  )
}
