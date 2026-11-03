import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Notice } from '../../ui/Notice'
import { Loading } from '../../ui/Spinner'
import { ContentLanguageProvider } from '../language/ContentLanguage'
import { openListeningSession } from '../recording/capture'
import type { AttemptResult, RoundState } from './api'
import { formatClock, isLow, remainingMs } from './clock'
import { bindWake, ExamController, type ExamView } from './examController'
import { FeedbackView } from './feedback/FeedbackView'
import { GiveUpControl } from './feedback/GiveUpControl'
import { getAttemptReport, type AttemptReport } from './reports'
import { ExamActions, type ExamButton } from './exam/ExamActions'
import { ExamHeader } from './exam/ExamHeader'
import { FailureNotice, FailureTimer } from './exam/FailurePanel'
import { LevelBars } from './exam/LevelBars'
import { PausePanel } from './exam/PausePanel'
import { ProcessingPanel, ProcessingTimer } from './exam/ProcessingPanel'
import { QuestionCard } from './exam/QuestionCard'
import { ResumePanel } from './exam/ResumePanel'
import { RoundTop, type StageIndex } from './exam/RoundTop'
import { TimerRing } from './exam/TimerRing'
import { prefetchRound, startReading } from './questionAudio'
import { readNoiseFloor } from './session'
import { timersOf } from './setup'
import { roundStrings } from './strings'
import { guardPageLeave, uploadQueue, type UploadQueue } from './uploads'
import { useExamKeys, type ExamKeyHandlers } from './useExamKeys'

const t = roundStrings.exam
const r = roundStrings.repetition

export interface ExamPageProps {
  /** Opens the microphone session of a question; tests pass a fake. */
  open?: typeof openListeningSession
  /** The page clock (performance.now()); tests pass their own. */
  now?: () => number
  read?: typeof startReading
  /** Prepares the voice of the coming questions; tests pass none. */
  prefetch?: (state: RoundState) => void
  uploads?: UploadQueue
  pollMs?: number
}

export function ExamPage(props: ExamPageProps) {
  const roundId = Number(useParams().roundId)
  return <ExamScreen key={roundId} roundId={roundId} {...props} />
}

/** The round says which language its questions and feedback are in; Polish until it loads (or for a round from before M6). */
function ExamScreen(props: ExamPageProps & { roundId: number }) {
  const [language, setLanguage] = useState<string | null>(null)
  return (
    <ContentLanguageProvider code={language}>
      <ExamScreenBody {...props} onLanguage={setLanguage} />
    </ContentLanguageProvider>
  )
}

const REPLAY_ICON = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 10a7 7 0 1 0 2.1-5" />
    <path d="M3 3.5V7h3.5" />
  </svg>
)

function stageOf(view: ExamView, round: RoundState): StageIndex {
  const followUps = round.config.follow_ups?.enabled ?? false
  if (round.part === 'followup' && round.phase !== 'feedback') return 4
  if (view.answerDeadline !== null) return 2
  switch (round.phase) {
    case 'question':
      return 0
    case 'preparation':
      return 1
    case 'recording':
      return 2
    case 'processing':
      return 3
    default:
      return followUps ? 5 : 4
  }
}

function ExamScreenBody({
  roundId,
  onLanguage,
  open = openListeningSession,
  now = () => performance.now(),
  read = startReading,
  prefetch = prefetchRound,
  uploads = uploadQueue,
  pollMs = 1500,
}: ExamPageProps & { roundId: number; onLanguage: (code: string | null) => void }) {
  const navigate = useNavigate()
  const [controller] = useState(
    () => new ExamController(roundId, { open, now, read, prefetch, uploads, pollMs, noiseFloorDb: readNoiseFloor() }),
  )
  const view = useSyncExternalStore(controller.subscribe, controller.getView)
  const failedUploads = useSyncExternalStore(uploads.subscribe, uploads.failed)
  const [subject, setSubject] = useState<string | null>(null)
  const round = view.round
  const roundLanguage = round?.config.language ?? null
  useLayoutEffect(() => onLanguage(roundLanguage), [onLanguage, roundLanguage])

  useEffect(() => {
    void controller.load()
    return () => controller.dispose()
  }, [controller])

  useEffect(() => bindWake(controller), [controller])

  useEffect(() => guardPageLeave(uploads), [uploads])
  useEffect(() => {
    void uploads.restore() // answers a reload interrupted while they were uploading
  }, [uploads])

  const subjectId = round?.subject_id
  useEffect(() => {
    if (subjectId === undefined) return
    let cancelled = false
    apiGet<{ name: string }>(`/subjects/${subjectId}`)
      .then((found) => !cancelled && setSubject(found.name))
      .catch(() => undefined) // the header just omits the name
    return () => {
      cancelled = true
    }
  }, [subjectId])

  // "Check microphone and continue" comes back with ?resume=1: resume once, then drop the query
  const resumeAsked = useRef(useSearchParams()[0].get('resume') === '1')
  useEffect(() => {
    if (!resumeAsked.current || !round) return
    resumeAsked.current = false
    navigate(`/rounds/${roundId}`, { replace: true })
    if (round.status === 'paused') void controller.resume()
  }, [round, roundId, navigate, controller])

  const over = round !== null && (round.status === 'abandoned' || round.status === 'finished' || round.phase === 'summary')
  useEffect(() => {
    if (over) navigate(`/rounds/${roundId}/summary`, { replace: true })
  }, [over, roundId, navigate])

  const paused = round?.status === 'paused'
  const phase = round?.phase
  const answering = view.answerDeadline !== null
  const audioOn = round !== null && round.config.question_audio.mode !== 'off'

  const keys: ExamKeyHandlers = {}
  if (round && round.status === 'active') {
    if (answering) Object.assign(keys, { space: () => void controller.finishAnswer(), n: () => void controller.dontKnow() })
    else if (phase === 'question') Object.assign(keys, { enter: () => controller.readMyself(), s: () => void controller.skip(), r: () => controller.replay() })
    else if (phase === 'preparation') Object.assign(keys, { space: () => controller.answerNow(), n: () => void controller.dontKnow(), s: () => void controller.skip(), r: () => controller.replay() })
    else if (phase === 'recording') Object.assign(keys, { space: () => void controller.finishAnswer(), n: () => void controller.dontKnow() })
    else if (phase === 'feedback') Object.assign(keys, { enter: () => void controller.next() })
    if (phase !== 'summary') keys.p = () => void controller.pause()
  } else if (paused) {
    keys.p = () => void controller.resume()
  }
  useExamKeys(keys)

  if (view.loadError) {
    return (
      <>
        <ExamHeader subject={null} timing="practice" onPause={null} pauseDisabled />
        <main className="exam">
          <Notice kind="error">{view.loadError}</Notice>
        </main>
      </>
    )
  }
  if (!round) {
    return (
      <>
        <ExamHeader subject={null} timing="practice" onPause={null} pauseDisabled />
        <main className="exam">
          <Loading>{t.loading}</Loading>
        </main>
      </>
    )
  }

  const total = round.total
  const index = round.current_index
  const question = round.current
  const feedbackPhase = phase === 'feedback' && !paused
  const statuses = round.items.map((item) => (item.status === 'done' ? 'done' : item.status === 'current' ? (feedbackPhase ? 'done' : 'now') : 'pending')) as (
    | 'done'
    | 'now'
    | 'pending'
  )[]
  const doneCount = statuses.filter((status) => status === 'done').length
  // an immediate repeat (Answer again) of the main question
  const repeating = round.part === 'main' && round.current_attempt?.attempt_kind === 'immediate'
  const title = repeating
    ? feedbackPhase
      ? r.countAgainFeedback(index + 1, total)
      : r.countAgain(index + 1, total)
    : feedbackPhase
      ? t.countFeedback(index + 1, total)
      : t.count(index + 1, total)
  const timers = timersOf(round)
  const prepMs = timers.prepSeconds * 1000
  const answerMs = timers.answerSeconds * 1000
  const followUpPart = round.part === 'followup'
  const stages = round.config.follow_ups?.enabled ? t.stagesFollowUp : t.stages
  const side = followUpPart && !feedbackPhase ? t.followUpOf(index + 1) : undefined

  const banners = (
    <>
      {view.error && <Notice kind="error">{view.error}</Notice>}
      {view.finishRetryAt !== null && (
        <Notice kind="warn">
          {t.finishRetry(Math.max(0, Math.ceil((view.finishRetryAt - view.nowMs) / 1000)))}{' '}
          <button type="button" className="btn small" onClick={() => controller.finishNow()}>
            {t.finishNow}
          </button>
        </Notice>
      )}
      {view.voiceFailed && <Notice kind="info">{view.voiceMessage ?? t.voiceFailed}</Notice>}
      {failedUploads.length > 0 && (
        <Notice kind="error">
          {t.uploadFailed}
          {failedUploads.some((item) => item.attemptId === round.current_attempt?.id) && phase === 'processing' && <> {t.uploadFailedCurrent}</>}
          <div className="actions">
            {failedUploads.map((item) => (
              <button key={item.attemptId} type="button" className="btn small" onClick={() => uploads.retry(item.attemptId)}>
                {t.uploadRetry}
              </button>
            ))}
          </div>
        </Notice>
      )}
    </>
  )

  const onPause = round.status === 'active' && phase !== 'summary' ? () => void controller.pause() : null
  const header = <ExamHeader
      subject={subject}
      timing={round.config.feedback_timing}
      roundType={round.config.round_type}
      onPause={onPause}
      pauseDisabled={view.busy}
    />

  if (paused && !view.pausedHere) {
    const stage = stageOf({ ...view, answerDeadline: null }, round)
    return (
      <>
        {header}
        <main className="exam">
          <RoundTop title={title} statuses={statuses} doneCount={doneCount} stage={stage} paused stages={stages} side={side} />
          {banners}
          <ResumePanel round={round} subject={subject} disabled={view.busy} onEnd={() => void controller.abandon()} />
        </main>
      </>
    )
  }

  if (paused) {
    const interrupted = round.current_attempt?.outcome === 'interrupted'
    const stage = stageOf({ ...view, answerDeadline: null }, round)
    return (
      <>
        {header}
        <main className="exam">
          <RoundTop title={title} statuses={statuses} doneCount={doneCount} stage={stage} paused stages={stages} side={side} />
          {banners}
          <PausePanel
            interrupted={interrupted}
            index={index}
            total={total}
            doneCount={doneCount}
            pausedMs={round.paused_ms + Math.max(0, view.nowMs - view.loadedAt)}
            pauseCount={round.pause_count}
            recordedMs={view.interruptedMs}
            disabled={view.busy}
            onResume={() => void controller.resume()}
            onEnd={() => void controller.abandon()}
          />
        </main>
      </>
    )
  }

  if (over || !question) {
    return (
      <>
        {header}
        <main className="exam">
          <Loading>{t.loading}</Loading>
        </main>
      </>
    )
  }

  const stage = stageOf(view, round)
  const top = <RoundTop title={title} statuses={statuses} doneCount={doneCount} stage={stage} stages={stages} side={side} />
  const replayButton = audioOn && (
    <button type="button" className="btn small replay" onClick={() => controller.replay()}>
      {REPLAY_ICON}
      {t.actions.replayQuestion} <kbd>{t.keys.r}</kbd>
    </button>
  )
  const card = (timer: ReactNode, replay: ReactNode = replayButton) => (
    <QuestionCard text={question.text} difficulty={question.difficulty} replay={replay} timer={timer} followUp={followUpPart} />
  )
  const dontKnow: ExamButton = { label: t.actions.dontKnow, key: t.keys.n, onClick: () => void controller.dontKnow(), disabled: view.busy }
  const skip: ExamButton = {
    label: t.actions.skip,
    wide: followUpPart ? t.actions.skipFollowUpWide : t.actions.skipWide,
    key: t.keys.s,
    onClick: () => void controller.skip(),
    disabled: view.busy,
  }
  const replay: ExamButton = { label: t.actions.replay, key: t.keys.r, onClick: () => controller.replay() }

  let body: ReactNode
  if (answering) {
    const remaining = remainingMs(view.answerDeadline as number, view.nowMs)
    const low = isLow(remaining, answerMs)
    const left = formatClock(remaining)
    body = (
      <>
        {card(
          <TimerRing
            status={t.recording.status}
            statusKind="live"
            fraction={remaining / answerMs}
            clock={left}
            sub={t.clockUnit.of(formatClock(answerMs))}
            ariaLabel={t.recording.ringLabel(left, formatClock(answerMs))}
            prep={false}
            low={low}
            note={
              low ? (
                <p className="low-note">{t.recording.lowNote(Math.ceil(remaining / 1000))}</p>
              ) : view.prepUsedMs !== null ? (
                <small className="muted">{t.recording.prepUsed(Math.round(view.prepUsedMs / 1000))}</small>
              ) : null
            }
          />,
          null,
        )}
        <div className="exam-help">
          <p>{view.startReason === 'auto' ? t.recording.helpAuto : t.recording.helpManual}</p>
          <LevelBars levels={view.levels} listening />
        </div>
        <ExamActions primary={{ label: t.recording.finish, key: t.keys.space, onClick: () => void controller.finishAnswer(), disabled: view.busy }} others={[dontKnow]} />
      </>
    )
  } else if (phase === 'question' && !view.questionShown) {
    // the question waits for its voice, so text and reading start together
    body = (
      <>
        <section className="panel exam-card exam-wait" aria-label={t.questionRegion}>
          <Loading>{t.question.preparing}</Loading>
          <p className="muted">{t.question.helpPreparing}</p>
        </section>
        <ExamActions
          primary={{ label: t.question.readMyself, wide: t.question.readMyselfWide, key: t.keys.enter, onClick: () => controller.readMyself(), disabled: view.busy }}
          others={[skip]}
        />
      </>
    )
  } else if (phase === 'question') {
    body = (
      <>
        {card(
          <TimerRing
            status={view.voicePreparing ? t.question.preparingVoice : view.reading ? t.question.reading : t.question.notReading}
            working={view.voicePreparing}
            statusKind="listen"
            fraction={1}
            clock={formatClock(prepMs)}
            sub={t.prep.toPrepare}
            ariaLabel={t.question.ringLabel(formatClock(prepMs))}
            prep
            note={<small className="muted">{view.reading ? t.question.startsAfter : t.question.startsNext}</small>}
          />,
          null,
        )}
        <div className="exam-help">
          <p>{view.reading ? t.question.helpReading : t.question.helpText}</p>
          <LevelBars levels={view.levels} listening={false} />
        </div>
        <ExamActions
          primary={audioOn ? { label: t.question.readMyself, wide: t.question.readMyselfWide, key: t.keys.enter, onClick: () => controller.readMyself(), disabled: view.busy } : undefined}
          others={[...(audioOn ? [replay] : []), skip]}
        />
      </>
    )
  } else if (phase === 'preparation') {
    const listening = view.listening !== 'off'
    const remaining = view.prepDeadline === null ? prepMs : remainingMs(view.prepDeadline, view.nowMs)
    const left = formatClock(remaining)
    body = (
      <>
        {card(
          <TimerRing
            status={listening ? t.prep.listening : t.prep.notListening}
            statusKind="listen"
            fraction={prepMs === 0 ? 0 : remaining / prepMs}
            clock={left}
            sub={listening ? t.prep.toPrepare : t.prep.ofTotal(formatClock(prepMs))}
            ariaLabel={t.prep.ringLabel(left, formatClock(prepMs))}
            prep
            note={<small className="muted">{t.prep.answerAfter(formatClock(answerMs))}</small>}
          />,
        )}
        <div className="exam-help">
          <p>
            {!listening
              ? t.prep.helpTimerOnly
              : followUpPart
                ? round.config.feedback_timing === 'exam'
                  ? t.followUp.helpPrepExam
                  : t.followUp.helpPrep
                : t.prep.help}
          </p>
          {listening && <LevelBars levels={view.levels} listening />}
        </div>
        <ExamActions primary={{ label: t.prep.answerNow, key: t.keys.space, onClick: () => controller.answerNow(), disabled: view.busy }} others={[dontKnow, skip]} />
      </>
    )
  } else if (phase === 'recording') {
    // a reload in the middle of an answer: the round is being paused
    body = <Loading>{t.loading}</Loading>
  } else {
    body = <ResultBody view={view} controller={controller} round={round} card={card} isLast={index === total - 1} />
  }

  return (
    <>
      {header}
      <main className="exam">
        {top}
        {banners}
        {body}
      </main>
    </>
  )
}

/** Processing and feedback of a practice answer, by what GET /result says. */
function ResultBody({
  view,
  controller,
  round,
  card,
  isLast,
}: {
  view: ExamView
  controller: ExamController
  round: RoundState
  card: (timer: ReactNode, replay?: ReactNode) => ReactNode
  isLast: boolean
}) {
  const result: AttemptResult | null = view.result
  const nextButton: ExamButton = {
    label: isLast ? roundStrings.exam.feedback.results : roundStrings.exam.feedback.next,
    key: t.keys.enter,
    onClick: () => void controller.next(),
    disabled: view.busy,
  }

  const failedHere = result?.status === 'failed'
  if ((!result || result.status === 'processing' || round.phase === 'processing') && !(failedHere && round.phase === 'processing')) {
    // a repeat (Answer again) never gets a follow-up
    const withFollowUp =
      round.config.follow_ups?.enabled === true && round.part === 'main' && round.current_attempt?.attempt_kind !== 'immediate'
    const followUp = round.follow_up
    const mainReady = result?.status === 'ready'
    const continueButton: ExamButton = { label: t.followUp.continue, onClick: () => void controller.noFollowUp(), disabled: view.busy }
    return (
      <>
        {card(<ProcessingTimer result={result} />, null)}
        {withFollowUp && followUp?.status === 'failed' && (
          <Notice kind="warn">
            <strong>{t.followUp.failed}</strong> {followUp.failure?.message}
          </Notice>
        )}
        <ProcessingPanel result={result} followUp={withFollowUp ? (followUp ?? null) : undefined} exam={round.config.feedback_timing === 'exam'} />
        {withFollowUp && mainReady && (
          <ExamActions
            primary={
              followUp?.status === 'failed'
                ? {
                    label: view.retryingFollowUp ? t.followUp.retrying : t.followUp.retry,
                    onClick: () => void controller.retryFollowUp(),
                    disabled: view.retryingFollowUp || view.busy,
                  }
                : undefined
            }
            others={[continueButton]}
          />
        )}
      </>
    )
  }
  if (!result) return null
  if (result.status === 'failed') {
    const retryable = result.failure?.retryable ?? false
    const continueButton: ExamButton = {
      label: t.failure.continue,
      wide: t.failure.continueWide,
      // in processing (a failed main answer with follow-ups on) "next" is not available yet
      onClick: () => void (round.phase === 'processing' ? controller.noFollowUp() : controller.next()),
      disabled: view.busy || view.givingUp,
    }
    // a follow-up answer cannot be given up; held uploads do not hide it (send again, or give up)
    const canGiveUp = round.part === 'main'
    return (
      <>
        <div className="stack">
          {card(<FailureTimer result={result} />, null)}
          <FailureNotice result={result} note={view.mayHaveCharged ? roundStrings.recovery.mayHaveCharged : null} />
        </div>
        <ExamActions
          primary={retryable ? { label: view.retrying ? t.failure.retrying : t.failure.retry, onClick: () => void controller.retryResult(), disabled: view.retrying || view.busy || view.givingUp } : { ...continueButton, key: t.keys.enter }}
          others={retryable ? [continueButton] : []}
        >
          {canGiveUp && (
            <GiveUpControl onGiveUp={() => void controller.giveUpResult()} busy={view.givingUp} disabled={view.busy || view.retrying} />
          )}
        </ExamActions>
      </>
    )
  }
  const again = round.can_answer_again ? (
    <AgainBox noAnswer={result.outcome === 'dont_know' || result.outcome === 'skipped'} disabled={view.busy} onClick={() => void controller.answerAgain()} />
  ) : null
  return <PracticeFeedback attemptId={round.current_attempt?.id ?? result.attempt_id} next={nextButton} again={again} />
}

/** "Answer again" (feedback.html, feedback--no-answer.html): the same main question once more; never the primary. */
function AgainBox({ noAnswer, disabled, onClick }: { noAnswer: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <section className="box tint flow" aria-labelledby="h-again">
      <h2 id="h-again">{r.againTitle}</h2>
      <p className="small">{noAnswer ? r.againTextNoAnswer : r.againText}</p>
      <button type="button" className="btn" onClick={onClick} disabled={disabled}>
        {REPLAY_ICON}
        {r.againButton}
      </button>
    </section>
  )
}

/** The full feedback of a processed practice answer, read from the reports endpoint. */
function PracticeFeedback({ attemptId, next, again }: { attemptId: number; next: ExamButton; again: ReactNode }) {
  const [report, setReport] = useState<AttemptReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getAttemptReport(attemptId)
      .then((found) => !cancelled && setReport(found))
      .catch((failure: unknown) => !cancelled && setError(failure instanceof Error ? failure.message : roundStrings.review.loadError))
    return () => {
      cancelled = true
    }
  }, [attemptId])
  return (
    <>
      {error && <Notice kind="error">{error}</Notice>}
      {!report && !error && <Loading>{roundStrings.review.loading}</Loading>}
      {report && <FeedbackView report={report} again={again} />}
      <ExamActions primary={next} />
    </>
  )
}
