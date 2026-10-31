import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { analyzeRecording, uploadRecording, type VadResult } from './api'
import { openListeningSession, type ListeningSession } from './capture'
import { SILENCE_DB } from './levels'
import { formatDb, recordingStrings } from './strings'

const t = recordingStrings.micCheck

export const CALIBRATION_MS = 3000
export const PREPARATION_S = 10
export const MAX_RECORDING_MS = 8000
const METER_MIN_DB = -100
const METER_REFRESH_MS = 100

type Phase =
  | 'start'
  | 'opening'
  | 'unavailable'
  | 'ready'
  | 'calibrating'
  | 'preparing'
  | 'recording'
  | 'processing'
  | 'done'

const LIVE_PHASES: Phase[] = ['ready', 'calibrating', 'preparing', 'recording']

const BAR_HEIGHTS = [8, 12, 18, 22, 16, 20, 10, 14, 9, 12, 7, 10]

function LevelMeter({ levelDb }: { levelDb: number }) {
  const clamped = Math.max(METER_MIN_DB, Math.min(0, levelDb))
  const percent = ((clamped - METER_MIN_DB) / -METER_MIN_DB) * 100
  const on = Math.round((percent / 100) * BAR_HEIGHTS.length)
  return (
    <div className="field">
      <span className="label" aria-hidden="true">
        {t.meterLabel}
      </span>
      <div
        className="level wide"
        role="meter"
        aria-label={t.meterLabel}
        aria-valuemin={METER_MIN_DB}
        aria-valuemax={0}
        aria-valuenow={Math.round(clamped)}
        aria-valuetext={`${formatDb(clamped)} dB`}
      >
        <span className="num">{formatDb(clamped)} dB</span>
        <span aria-hidden="true">
          {BAR_HEIGHTS.map((height, i) => (
            <em key={i} className={i < on ? 'on' : undefined} style={{ height }} />
          ))}
        </span>
      </div>
    </div>
  )
}

const ICON_PROPS = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const

const OK_ICON = (
  <svg {...ICON_PROPS}>
    <path d="M4.5 10.5l3.5 3.5 7.5-8" />
  </svg>
)

const ERROR_ICON = (
  <svg {...ICON_PROPS}>
    <circle cx="10" cy="10" r="7" />
    <path d="M10 6.5v4" />
    <path d="M10 13.3v.1" />
  </svg>
)

const WARN_ICON = (
  <svg {...ICON_PROPS}>
    <path d="M10 3.5l7 12.5H3z" />
    <path d="M10 8.5v3.5" />
    <path d="M10 14.2v.1" />
  </svg>
)

function StepHead({ n, title }: { n: number; title: string }) {
  return (
    <h2>
      <span className="visually-hidden">{t.step(n)}</span>
      {title}
    </h2>
  )
}

export interface MicCheckContext {
  /** The microphone can be heard: access was granted. */
  ready: boolean
  /** The calibrated noise floor, or null before it was measured. */
  floorDb: number | null
}

interface MicCheckProps {
  open?: typeof openListeningSession
  /** Round context: breadcrumbs above the title. */
  crumbs?: ReactNode
  /** Round context: the actions that leave the page. */
  footer?: (ctx: MicCheckContext) => ReactNode
}

export function MicCheckPage({ open = openListeningSession, crumbs, footer }: MicCheckProps) {
  const [phase, setPhase] = useState<Phase>('start')
  const [reason, setReason] = useState<string | null>(null)
  const [resampled, setResampled] = useState(false)
  const [level, setLevel] = useState(SILENCE_DB)
  const [floor, setFloor] = useState<number | null>(null)
  const [countdown, setCountdown] = useState(PREPARATION_S)
  const [startedBy, setStartedBy] = useState<'speech' | 'timer' | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [vad, setVad] = useState<VadResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const sessionRef = useRef<ListeningSession | null>(null)
  const levelRef = useRef(SILENCE_DB)
  const floorRef = useRef<number | null>(null)
  const countdownTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const mounted = useRef(true)

  const clearTimers = () => {
    if (countdownTimer.current) clearInterval(countdownTimer.current)
    if (stopTimer.current) clearTimeout(stopTimer.current)
    countdownTimer.current = null
    stopTimer.current = null
  }

  const closeSession = () => {
    sessionRef.current?.dispose()
    sessionRef.current = null
  }

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      clearTimers()
      closeSession()
    }
  }, [])

  useEffect(() => {
    if (!LIVE_PHASES.includes(phase)) return
    const id = setInterval(() => {
      setLevel(levelRef.current)
      levelRef.current = SILENCE_DB
    }, METER_REFRESH_MS)
    return () => clearInterval(id)
  }, [phase])

  useEffect(
    () => () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl)
    },
    [audioUrl],
  )

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !audioUrl) return
    audio.play()?.catch(() => {})
  }, [audioUrl])

  const allow = useCallback(async () => {
    setPhase('opening')
    setError(null)
    const session = await open(floorRef.current === null ? {} : { noiseFloorDb: floorRef.current })
    if (!mounted.current) {
      session.dispose()
      return
    }
    if (!session.available) {
      setReason(session.reason)
      setPhase('unavailable')
      return
    }
    sessionRef.current = session
    setResampled(session.resampled)
    // The meter shows the loudest level since its last refresh, so a short sound (a click, a
    // plosive) between two refreshes is never missed.
    session.onLevel((db) => {
      levelRef.current = Math.max(levelRef.current, db)
    })
    setPhase('ready')
  }, [open])

  const calibrate = async () => {
    const session = sessionRef.current
    if (!session) return
    setPhase('calibrating')
    const measured = await session.calibrate(CALIBRATION_MS)
    if (!mounted.current || sessionRef.current !== session) return
    floorRef.current = measured
    setFloor(measured)
    setPhase('ready')
  }

  const finish = async () => {
    const session = sessionRef.current
    if (!session) return
    clearTimers()
    const { wav, durationMs } = await session.stop()
    closeSession()
    if (!mounted.current) return
    setAudioUrl(durationMs > 0 ? URL.createObjectURL(wav) : null)
    setPhase('processing')
    try {
      const { id } = await uploadRecording(wav, 'mic_check')
      const result = await analyzeRecording(id)
      if (!mounted.current) return
      setVad(result)
    } catch (e) {
      if (!mounted.current) return
      setError(e instanceof Error ? e.message : recordingStrings.errors.upload)
    }
    setPhase('done')
  }

  const beginRecording = (by: 'speech' | 'timer') => {
    clearTimers()
    setStartedBy(by)
    setPhase('recording')
    stopTimer.current = setTimeout(() => void finish(), MAX_RECORDING_MS)
  }

  const startTest = () => {
    const session = sessionRef.current
    if (!session) return
    setStartedBy(null)
    setVad(null)
    setCountdown(PREPARATION_S)
    setPhase('preparing')
    session.startListening(() => beginRecording('speech'))
    let left = PREPARATION_S
    countdownTimer.current = setInterval(() => {
      left -= 1
      setCountdown(left)
      if (left <= 0 && sessionRef.current?.state === 'listening') {
        sessionRef.current.startRecording()
        beginRecording('timer')
      }
    }, 1000)
  }

  const again = () => {
    setAudioUrl(null)
    setVad(null)
    setError(null)
    setStartedBy(null)
    void allow()
  }

  const firstStart = vad?.speech_intervals[0]?.[0]
  const lastEnd = vad?.speech_intervals[vad.speech_intervals.length - 1]?.[1]

  const accessDone = LIVE_PHASES.includes(phase) || phase === 'processing' || phase === 'done'
  const currentStep =
    phase === 'ready' ? (floor === null ? 2 : 3) : phase === 'calibrating' ? 2 : accessDone ? 3 : 1
  const stepN = (n: number) => (
    <span className="step-n" aria-current={currentStep === n ? 'step' : undefined}>
      {n}
    </span>
  )

  return (
    <>
      {crumbs}
      <div className="page-head">
        <div>
          <h1 id="mic-check-title">{t.title}</h1>
          <p>{t.intro}</p>
        </div>
      </div>

      <ol className="step-list">
        <li>
          {stepN(1)}
          <div className="stack">
            <StepHead n={1} title={t.stepAccess} />
            {phase === 'start' && (
              <div className="actions">
                <button type="button" className="btn primary" onClick={() => void allow()}>
                  {t.allow}
                </button>
              </div>
            )}
            {phase === 'opening' && (
              <p role="status" className="lead">
                {t.opening}
              </p>
            )}
            {phase === 'unavailable' && (
              <div className="notice error" role="alert">
                {ERROR_ICON}
                <div>{reason}</div>
              </div>
            )}
            {accessDone && (
              <div className="notice ok">
                {OK_ICON}
                <span>{t.ready}</span>
              </div>
            )}
            {LIVE_PHASES.includes(phase) && (
              <>
                {resampled && <p className="small muted">{t.resampled}</p>}
                <LevelMeter levelDb={level} />
              </>
            )}
          </div>
        </li>

        <li>
          {stepN(2)}
          <div className="stack">
            <StepHead n={2} title={t.stepQuiet} />
            {!accessDone && <p className="muted">{t.afterAccess}</p>}
            {phase === 'ready' && floor === null && <p className="lead">{t.quietLead}</p>}
            {phase === 'calibrating' && (
              <p role="status" className="lead">
                {t.calibrating}
              </p>
            )}
            {floor !== null && (
              <div className="notice ok">
                {OK_ICON}
                <span className="num">{t.noiseFloor(floor)}</span>
              </div>
            )}
            {phase === 'ready' && (
              <div className="actions">
                <button type="button" className="btn" onClick={() => void calibrate()}>
                  {t.calibrate}
                </button>
              </div>
            )}
          </div>
        </li>

        <li>
          {stepN(3)}
          <div className="stack">
            <StepHead n={3} title={t.stepSample} />
            {!accessDone && <p className="muted">{t.afterAccess}</p>}
            {(phase === 'ready' || phase === 'calibrating') && <p className="lead">{t.sampleLead}</p>}
            {phase === 'ready' && (
              <div className="actions">
                <button type="button" className="btn primary" onClick={startTest}>
                  {t.listenTest}
                </button>
              </div>
            )}
            {phase === 'preparing' && (
              <>
                <div className="actions">
                  <span className="live listen">
                    <i aria-hidden="true" />
                    {t.listening}
                  </span>
                  <p role="timer" aria-label={t.preparation} aria-live="polite" className="num">
                    {t.countdown(countdown)}
                  </p>
                </div>
                <p className="lead">{t.listenHint}</p>
              </>
            )}
            {startedBy && (phase === 'recording' || phase === 'processing' || phase === 'done') && (
              <p role="status" className="lead">
                {startedBy === 'speech' ? t.speechDetected : t.timerStarted}
              </p>
            )}
            {phase === 'recording' && (
              <div className="actions">
                <span className="live">
                  <i aria-hidden="true">
                    <span className="visually-hidden">● </span>
                  </i>
                  {t.recording}
                </span>
                <button type="button" className="btn primary" onClick={() => void finish()} autoFocus>
                  {t.stop}
                </button>
              </div>
            )}

            {phase === 'processing' && (
              <p role="status" className="lead">
                {t.uploading}
              </p>
            )}
            {audioUrl && (phase === 'processing' || phase === 'done') && (
              <audio ref={audioRef} src={audioUrl} controls aria-label={t.playback} />
            )}
            {phase === 'done' &&
              vad &&
              (firstStart !== undefined && lastEnd !== undefined ? (
                <p className="small muted num">{t.speechRange(firstStart, lastEnd)}</p>
              ) : (
                <div className="notice warn">
                  {WARN_ICON}
                  <div>{t.noSpeech}</div>
                </div>
              ))}
            {error && (
              <div className="notice error" role="alert">
                {ERROR_ICON}
                <div>{error}</div>
              </div>
            )}
            {phase === 'done' && (
              <div className="actions">
                <button type="button" className="btn primary" onClick={again}>
                  {t.again}
                </button>
              </div>
            )}
          </div>
        </li>
      </ol>
      {footer?.({ ready: accessDone, floorDb: floor })}
    </>
  )
}
