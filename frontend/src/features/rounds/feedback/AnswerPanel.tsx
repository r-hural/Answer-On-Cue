import { useState } from 'react'
import { Notice } from '../../../ui/Notice'
import { recordingAudioUrl } from '../../recording/api'
import { formatClock } from '../clock'
import { putTranscriptReport, type AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { AudioPlayer, PauseTimeline, type AudioPlayerControl } from './AudioPlayer'
import { timelineMarks } from './format'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review

function ReportForm({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const saved = report.transcript_report
  const [text, setText] = useState(saved?.corrected_text ?? report.result.transcript ?? '')
  const [state, setState] = useState<'idle' | 'sending' | 'saved' | 'failed'>(saved ? 'saved' : 'idle')
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    setState('sending')
    setError(null)
    try {
      await putTranscriptReport(report.attempt_id, text)
      setState('saved')
    } catch (failure) {
      setState('failed')
      setError(failure instanceof Error ? failure.message : t.reportFailed)
    }
  }

  return (
    <details className="disclosure">
      <summary>{t.reportSummary}</summary>
      <div className="field">
        <label className="label" htmlFor="fix">
          {t.reportLabel}
        </label>
        <textarea
          className="textarea content"
          id="fix"
          lang={cl.bcp47}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            if (state === 'saved') setState('idle')
          }}
        />
      </div>
      <button type="button" className="btn small" disabled={state === 'sending' || text.trim() === ''} onClick={() => void send()}>
        {state === 'sending' ? t.reportSending : t.reportSend}
      </button>
      {state === 'saved' && <Notice kind="ok">{t.reportSaved}</Notice>}
      {state === 'failed' && <Notice kind="error">{error ?? t.reportFailed}</Notice>}
    </details>
  )
}

/** The recording with playback, the pause timeline, the transcript and the transcription-mistake report. */
export function AnswerPanel({ report, player }: { report: AttemptReport; player: AudioPlayerControl }) {
  const cl = useContentLanguage()
  const { result } = report
  const recording = result.recording
  const noSpeech = result.assessment?.status === 'no_speech'
  const metrics = result.metrics
  const marks = metrics && recording ? timelineMarks(metrics.pauses, recording.duration_ms) : []
  const transcript = result.transcript

  if (!recording && !transcript) return null
  return (
    <section className="panel flow" aria-labelledby="h-answer">
      <h2 id="h-answer">{t.answer}</h2>
      {recording && (
        <AudioPlayer
          player={player}
          src={recordingAudioUrl(recording.id)}
          label={t.playerLabel(formatClock(recording.duration_ms))}
        />
      )}
      {recording && !noSpeech && metrics && (
        <PauseTimeline marks={marks} durationMs={recording.duration_ms} onSeek={(ms) => { player.seek(ms); player.play() }} />
      )}
      {!noSpeech && transcript && (
        <>
          <h3>{t.transcript}</h3>
          <div className="transcript" lang={cl.bcp47}>
            <p>{cl.typeset(transcript)}</p>
          </div>
          <ReportForm report={report} />
        </>
      )}
    </section>
  )
}
