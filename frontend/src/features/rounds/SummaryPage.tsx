import { usd } from '../costs/format'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import { ContentLanguageProvider } from '../language/ContentLanguage'
import {
  generateExtensionQuestions,
  getExtensionOffer,
  getRetryOffer,
  giveUpAttempt,
  GIVEN_UP,
  recoverRound,
  retryAttempt,
  startExtension,
  startRetry,
  type ExtensionOffer,
  type RetryOffer,
} from './api'
import { formatClock } from './clock'
import { dateTime, timeOnly } from './feedback/format'
import { getRoundReport, type RoundReport } from './reports'
import { roundTypeLabel } from './roundType'
import { roundStrings } from './strings'
import { roundPolicyLabel } from './sourcePolicy'
import {
  ExtensionBox,
  ExtensionColumn,
  extensionHasAction,
  extensionShown,
  FollowUpsBox,
  KeepPractisingPanel,
  KnowledgePanel,
  RepeatBox,
  RetryBox,
  RetryColumn,
  retryShown,
  SpeedSummary,
} from './summary/Panels'
import { QuestionRows } from './summary/QuestionRows'
import { RetrySummary } from './summary/RetrySummary'
import { uploadQueue, type UploadQueue } from './uploads'
import { Loading } from '../../ui/Spinner'

const t = roundStrings.summary
const exam = roundStrings.exam
const a = roundStrings.adaptive
const r = roundStrings.repetition
const DEFAULT_POLL_MS = 3000
const MAX_POLL_MS = 5 * 60 * 1000
/** Question generation gets its own, longer clock. */
const MAX_GENERATION_MS = 10 * 60 * 1000
const MIX_ORDER = ['weak', 'unpractised', 'review']

export interface SummaryPageProps {
  pollMs?: number
  uploads?: UploadQueue
}

function Title({ report }: { report: RoundReport }) {
  if (report.withheld) return <>{t.titleRunning}</>
  if (report.parent_round_id != null && report.status === 'finished') {
    return <>{report.config.round_type === 'retry' ? r.finished : a.finished}</>
  }
  return <>{report.status === 'abandoned' ? t.titleEarly : t.title}</>
}

function ProvisionalNotice({ report }: { report: RoundReport }) {
  const ready = report.items.filter((item) => item.status === 'ready' && item.outcome !== GIVEN_UP).length
  const failed = report.items.filter((item) => item.status === 'failed').length
  const processing = report.items.filter((item) => item.status === 'processing').length
  if (failed + processing === 0) {
    return (
      <Notice kind="warn">
        <strong>{t.provisionalTitle}</strong> {t.provisionalEarly}
      </Notice>
    )
  }
  const parts = [t.assessedCount(ready)]
  if (failed > 0) parts.push(t.failedCount(failed))
  if (processing > 0) parts.push(t.processingCount(processing))
  return (
    <Notice kind="warn">
      <strong>{t.provisionalTitle}</strong> {parts.join('; ')}. {t.provisionalTail} {failed > 0 ? t.provisionalRetry : ''}
    </Notice>
  )
}

/** Why an adaptive round asked what it asked, from the counts the server stored. */
function MixNote({ report }: { report: RoundReport }) {
  const counts = report.selection_counts
  if (report.config.round_type !== 'adaptive' || !counts) return null
  if (counts.balanced) return <p className="note">{a.balanced}</p>
  const parts = MIX_ORDER.filter((key) => (counts[key] ?? 0) > 0).map((key) => `${counts[key]} ${a.mixParts[key]}`)
  return <p className="note">{a.mix(parts)}</p>
}

/** The extension is offered only on its round's summary, and only when the option was on. */
const offersExtension = (report: RoundReport) =>
  !report.withheld && report.config.extension?.enabled === true && report.config.round_type !== 'extension'

/** The retry round is offered only on its round's summary, never for a retry round or an extension. */
const offersRetry = (report: RoundReport) =>
  !report.withheld &&
  report.config.retry_round === true &&
  report.config.round_type !== 'retry' &&
  report.config.round_type !== 'extension'

/** The round's results: scores, bands, speed and fluency, one row per question; shared by "just ended" and History. */
export function SummaryPage({ pollMs = DEFAULT_POLL_MS, uploads = uploadQueue }: SummaryPageProps) {
  const roundId = Number(useParams().roundId)
  useEffect(() => {
    void uploads.restore() // answers a reload interrupted while they were uploading
  }, [uploads])
  return <Summary key={roundId} roundId={roundId} pollMs={pollMs} />
}

/** The round report says which language its questions are in; Polish until it loads (or for a round from before M6). */
function Summary(props: { roundId: number; pollMs: number }) {
  const [language, setLanguage] = useState<string | null>(null)
  return (
    <ContentLanguageProvider code={language}>
      <SummaryBody {...props} onLanguage={setLanguage} />
    </ContentLanguageProvider>
  )
}

function SummaryBody({ roundId, pollMs, onLanguage }: { roundId: number; pollMs: number; onLanguage: (code: string | null) => void }) {
  const [report, setReport] = useState<RoundReport | null>(null)
  const reportLanguage = report?.config.language ?? null
  useLayoutEffect(() => onLanguage(reportLanguage), [onLanguage, reportLanguage])
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [givingUpId, setGivingUpId] = useState<number | null>(null)
  const [offer, setOffer] = useState<ExtensionOffer | null>(null)
  const [extensionBusy, setExtensionBusy] = useState(false)
  const [generationSlow, setGenerationSlow] = useState(false)
  const [retryOffer, setRetryOffer] = useState<RetryOffer | null>(null)
  const [retryBusy, setRetryBusy] = useState(false)
  const watching = useRef(0)
  const since = useRef(0)
  const recovered = useRef(false)
  const navigate = useNavigate()

  const readOffer = useCallback(async (): Promise<ExtensionOffer | null> => {
    try {
      const next = await getExtensionOffer(roundId)
      setOffer(next)
      return next
    } catch {
      return null // the panel keeps what it showed; the next read may succeed
    }
  }, [roundId])

  const readRetryOffer = useCallback(async (): Promise<void> => {
    try {
      setRetryOffer(await getRetryOffer(roundId))
    } catch {
      // the column keeps what it showed; the next read may succeed
    }
  }, [roundId])

  const load = useCallback(async (): Promise<void> => {
    try {
      const next = await getRoundReport(roundId)
      setReport(next)
      setError(null)
      if (offersExtension(next)) await readOffer()
      if (offersRetry(next)) await readRetryOffer()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t.loadError)
    }
  }, [roundId, readOffer, readRetryOffer])

  // an ended round with answers cut off by a restart: certain ones resume by themselves (once)
  const cutOff =
    report !== null && !report.withheld && report.items.some((item) => item.status === 'failed' || item.status === 'processing')
  useEffect(() => {
    if (!cutOff || recovered.current) return
    recovered.current = true
    recoverRound(roundId)
      .then(() => load())
      .catch(() => undefined) // the queue of "Try again" stays as it is
  }, [cutOff, roundId, load])

  // read the report; again every few seconds while an answer is still being processed
  // (not while a running exam round withholds its results: nothing would change on screen)
  const waiting =
    report === null
      ? error === null
      : !report.withheld &&
        (report.items.some((item) => item.status === 'processing') ||
          (report.follow_ups?.processing ?? 0) > 0 ||
          offer?.status === 'waiting' ||
          retryOffer?.status === 'waiting')
  useEffect(() => {
    if (!waiting) return
    const ticket = (watching.current += 1)
    let timer: ReturnType<typeof setTimeout> | undefined
    if (since.current === 0) since.current = Date.now()
    const tick = async () => {
      await load()
      if (watching.current !== ticket) return
      if (Date.now() - since.current < MAX_POLL_MS) timer = setTimeout(() => void tick(), pollMs)
    }
    void tick()
    return () => {
      watching.current += 1
      if (timer) clearTimeout(timer)
    }
  }, [waiting, load, pollMs])

  // while new questions are generated, read the offer on its own clock; then one manual check
  const generating = offer?.status === 'generating'
  useEffect(() => {
    if (!generating) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const started = Date.now()
    const tick = async () => {
      const next = await readOffer()
      if (stopped || (next !== null && next.status !== 'generating')) return
      if (Date.now() - started < MAX_GENERATION_MS) timer = setTimeout(() => void tick(), pollMs)
      else setGenerationSlow(true)
    }
    timer = setTimeout(() => void tick(), pollMs)
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [generating, readOffer, pollMs])

  const startExtra = async () => {
    setExtensionBusy(true)
    setActionError(null)
    try {
      const child = await startExtension(roundId)
      navigate(`/practice/${child.id}/microphone`)
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : a.failed)
      setExtensionBusy(false)
      void readOffer()
    }
  }

  const startRetryRound = async () => {
    setRetryBusy(true)
    setActionError(null)
    try {
      const child = await startRetry(roundId)
      navigate(`/practice/${child.id}/microphone`)
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : r.retryFailed)
      setRetryBusy(false)
      void readRetryOffer()
    }
  }

  const generate = async () => {
    setExtensionBusy(true)
    setActionError(null)
    try {
      const { job_id } = await generateExtensionQuestions(roundId)
      setGenerationSlow(false)
      setOffer((current) => (current ? { ...current, status: 'generating', job_id } : current))
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : a.failed)
    } finally {
      setExtensionBusy(false)
    }
  }

  const retry = async (attemptId: number) => {
    setRetryingId(attemptId)
    setActionError(null)
    try {
      await retryAttempt(attemptId)
      since.current = Date.now()
      await load()
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : t.retryFailed)
    } finally {
      setRetryingId(null)
    }
  }

  const giveUp = async (attemptId: number) => {
    setGivingUpId(attemptId)
    setActionError(null)
    try {
      await giveUpAttempt(attemptId)
      await load()
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : t.giveUpFailed)
    } finally {
      setGivingUpId(null)
    }
  }

  if (error && !report) {
    return <Notice kind="error">{error}</Notice>
  }
  if (!report) return <Loading>{t.loading}</Loading>

  const early = report.status === 'abandoned'
  const ended = report.status === 'finished' || early
  const when = report.finished_at ? `${dateTime(report.started_at)}–${timeOnly(report.finished_at)}` : dateTime(report.started_at)
  const unresolved = report.items.some((item) => item.status === 'failed')
  // one primary per screen: a retryable answer's "Try again" comes first
  const rowRetryShown = report.items.some((item) => item.status === 'failed' && item.failure?.retryable && item.attempt_id !== null)
  const retryRound = report.config.round_type === 'retry'
  const comparison = report.retry_comparison ?? null
  const extensionOffer = offersExtension(report) && extensionShown(offer) ? offer : null
  // one primary: the rows' "Try again" first, then the extension's action, then the retry round's start
  const extensionPrimary = !rowRetryShown
  const retryPrimary = !rowRetryShown && !extensionHasAction(extensionOffer)

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <Link to="/progress/history">{t.crumbHistory}</Link> › {dateTime(report.started_at)}
          </p>
          <h1>
            <Title report={report} />
          </h1>
          {retryRound && comparison ? (
            <p>
              {r.retryOf(dateTime(comparison.parent_started_at))} ·{' '}
              <span className="content">
                {plNbsp(report.subject_name)}
              </span>{' '}
              · {r.belowGood(report.total)} · {r.sameLimits} · {roundPolicyLabel(report.config.source_policy)}
            </p>
          ) : (
            <p>
              <span className="content">
                {plNbsp(report.subject_name)}
              </span>{' '}
              · {roundTypeLabel(report.config.round_type)} · {report.config.feedback_timing === 'exam' ? exam.feedbackExam : exam.feedbackPractice} · {roundPolicyLabel(report.config.source_policy)} · {when} ·{' '}
              {early ? t.questionsAsked(report.asked, report.total) : t.questions(report.total)}
            </p>
          )}
        </div>
        <div className="actions">
          {report.parent_round_id != null && (
            <Link className="btn" to={`/rounds/${report.parent_round_id}/summary`}>
              {retryRound ? r.originalRound : a.backToRound}
            </Link>
          )}
          <Link className="btn" to="/">
            {t.home}
          </Link>
          <Link className="btn" to="/practice">
            {t.newRound}
          </Link>
        </div>
      </div>

      <MixNote report={report} />

      {actionError && <Notice kind="error">{actionError}</Notice>}

      {report.withheld ? (
        <Notice kind="info">
          {t.withheld} <Link to={`/rounds/${roundId}`}>{t.backToRound}</Link>
        </Notice>
      ) : (
        <>
          {report.provisional && <ProvisionalNotice report={report} />}

          <KeepPractisingPanel
            extension={
              extensionOffer && (
                <ExtensionColumn
                  offer={extensionOffer}
                  subjectId={report.subject_id}
                  actions={{
                    primary: extensionPrimary,
                    busy: extensionBusy,
                    slow: generationSlow,
                    onStart: () => void startExtra(),
                    onGenerate: () => void generate(),
                    onCheck: () => void readOffer(),
                  }}
                />
              )
            }
            retry={
              offersRetry(report) &&
              retryShown(retryOffer) && (
                <RetryColumn offer={retryOffer} actions={{ primary: retryPrimary, busy: retryBusy, onStart: () => void startRetryRound() }} />
              )
            }
          />

          {retryRound && comparison ? (
            <RetrySummary report={report} primary={!rowRetryShown} />
          ) : (
            <div className="cols-2 mt-8">
              <KnowledgePanel report={report} />
              <SpeedSummary report={report} />
            </div>
          )}

          <section className="section" aria-labelledby="h-questions">
            <h2 id="h-questions">{t.questionsHeading}</h2>
            <QuestionRows
              roundId={roundId}
              items={report.items}
              retryingId={retryingId}
              onRetry={(id) => void retry(id)}
              onGiveUp={ended && !report.withheld ? (id) => void giveUp(id) : undefined}
              givingUpId={givingUpId}
            />
            {report.ai_cost_usd != null && (
              <dl className="facts mt-4">
                <dt>{roundStrings.costs.aiCost}</dt>
                <dd className="num">{usd(report.ai_cost_usd)}</dd>
              </dl>
            )}
            {(unresolved || early) && (
              <p className="note mt-4">
                {t.unresolvedNote} {early ? t.earlyNote : ''}
              </p>
            )}
          </section>

          {(report.follow_ups ||
            report.extension ||
            report.repeats ||
            report.retry ||
            report.pause_count > 0 ||
            report.restarted_ordinals.length > 0) && (
            <div className="cols-3 section">
              <FollowUpsBox report={report} />
              <RepeatBox report={report} />
              <ExtensionBox report={report} />
              <RetryBox report={report} />
              {(report.pause_count > 0 || report.restarted_ordinals.length > 0) && (
              <section className="box tint flow" aria-labelledby="h-interruptions">
                <h2 id="h-interruptions">{t.interruptions}</h2>
                <p>
                  {[
                    report.pause_count > 0 ? t.pauses(report.pause_count, formatClock(report.paused_ms)) : null,
                    report.restarted_ordinals.length > 0 ? t.restartedQuestions(report.restarted_ordinals) : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                <p className="small muted">{t.interruptionsNote}</p>
              </section>
              )}
            </div>
          )}
        </>
      )}
    </>
  )
}
