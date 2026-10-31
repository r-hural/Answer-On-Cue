import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link, useParams } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import { ContentLanguageProvider } from '../language/ContentLanguage'
import { giveUpAttempt, retryAttempt } from './api'
import { FeedbackView, type FeedbackActions } from './feedback/FeedbackView'
import { dateTime } from './feedback/format'
import { getAttemptReport, getRoundReport, type AttemptReport } from './reports'
import { roundTypeLabel } from './roundType'
import { roundStrings } from './strings'
import { uploadQueue, type UploadQueue } from './uploads'

const t = roundStrings.review
const exam = roundStrings.exam
/** The report is read again this often while the answer is still being processed. */
const DEFAULT_POLL_MS = 3000
const MAX_POLL_MS = 5 * 60 * 1000

export interface AnswerPageProps {
  uploads?: UploadQueue
  pollMs?: number
}

/** The feedback of one past answer, in the app shell (History › round › question). */
export function AnswerPage({ uploads = uploadQueue, pollMs = DEFAULT_POLL_MS }: AnswerPageProps) {
  const attemptId = Number(useParams().attemptId)
  return <Answer key={attemptId} attemptId={attemptId} uploads={uploads} pollMs={pollMs} />
}

/** The language of the answer comes from its round report; Polish until it loads (or for a round from before M6). */
function Answer(props: { attemptId: number; uploads: UploadQueue; pollMs: number }) {
  const [language, setLanguage] = useState<string | null>(null)
  return (
    <ContentLanguageProvider code={language}>
      <AnswerBody {...props} onLanguage={setLanguage} />
    </ContentLanguageProvider>
  )
}

function AnswerBody({
  attemptId,
  uploads,
  pollMs,
  onLanguage,
}: {
  attemptId: number
  uploads: UploadQueue
  pollMs: number
  onLanguage: (code: string | null) => void
}) {
  const [report, setReport] = useState<AttemptReport | null>(null)
  const [started, setStarted] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [givingUp, setGivingUp] = useState(false)
  const failedUploads = useSyncExternalStore(uploads.subscribe, uploads.failed)
  const watching = useRef(0)
  const since = useRef(0)

  const load = useCallback(async (): Promise<AttemptReport | null> => {
    try {
      const next = await getAttemptReport(attemptId)
      setReport(next)
      setError(null)
      return next
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t.loadError)
      return null
    }
  }, [attemptId])

  // the links follow the attempt's own round, whatever round the address names
  const roundId = report?.round_id ?? null
  useEffect(() => {
    if (roundId === null) return
    let cancelled = false
    getRoundReport(roundId)
      .then((round) => {
        if (cancelled) return
        setStarted(round.started_at)
        onLanguage(round.config.language ?? null)
      })
      .catch(() => undefined) // the breadcrumb just says "Round"
    return () => {
      cancelled = true
    }
  }, [roundId, onLanguage])

  // read the report, and again every few seconds while the answer is processing
  const status = report?.result.status
  const waiting = report === null ? error === null : status === 'processing'
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

  const retry = async () => {
    setRetrying(true)
    setActionError(null)
    try {
      await retryAttempt(attemptId)
      since.current = Date.now()
      await load()
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : exam.errors.unknown)
    } finally {
      setRetrying(false)
    }
  }

  const giveUp = async () => {
    setGivingUp(true)
    setActionError(null)
    try {
      await giveUpAttempt(attemptId)
      await load()
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : roundStrings.summary.giveUpFailed)
    } finally {
      setGivingUp(false)
    }
  }

  const resend = async () => {
    setRetrying(true)
    setActionError(null)
    try {
      if (await uploads.retry(attemptId)) {
        await retryAttempt(attemptId)
        since.current = Date.now()
        await load()
      } else {
        setActionError(exam.uploadFailed)
      }
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : exam.errors.unknown)
    } finally {
      setRetrying(false)
    }
  }

  if (error && !report) {
    return (
      <Notice kind="error">
        {error}
      </Notice>
    )
  }
  if (!report) return <p role="status">{t.loading}</p>

  const { result } = report
  const failure = result.failure
  const followUp = report.role === 'followup'
  const main = followUp && report.parent_attempt_id ? `/rounds/${roundId}/answers/${report.parent_attempt_id}` : null
  const held = failedUploads.some((item) => item.attemptId === attemptId)
  // only a main answer of an ended round, never while its results are withheld
  const ended = report.round_status === 'finished' || report.round_status === 'abandoned'
  const canGiveUp = result.status === 'failed' && !followUp && ended && !result.withheld
  const actions: FeedbackActions = {
    onRetry: result.status === 'failed' && failure?.retryable ? () => void retry() : null,
    retrying,
    onResend: result.status === 'failed' && failure?.kind === 'audio_missing' && held ? () => void resend() : null,
    onGiveUp: canGiveUp ? () => void giveUp() : null,
    givingUp,
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <Link to="/progress/history">{t.crumbHistory}</Link> ›{' '}
            <Link to={`/rounds/${roundId}/summary`}>{started ? dateTime(started) : t.crumbRound}</Link> ›{' '}
            {followUp ? (
              <>
                {main ? <Link to={main}>{t.crumbQuestion(report.ordinal)}</Link> : t.crumbQuestion(report.ordinal)} › {t.followUp.crumb}
              </>
            ) : (
              t.crumbQuestion(report.ordinal)
            )}
          </p>
          <h1>{followUp ? t.followUp.titleOf(report.ordinal) : t.titleOf(report.ordinal, report.total)}</h1>
          <p>
            <span className="content">
              {plNbsp(report.subject_name)}
            </span>{' '}
            · {roundTypeLabel(report.round_type)} · {report.feedback_timing === 'exam' ? exam.feedbackExam : exam.feedbackPractice}
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to={`/rounds/${roundId}/summary`}>
            {t.toSummary}
          </Link>
        </div>
      </div>

      {actionError && <Notice kind="error">{actionError}</Notice>}

      {result.withheld ? (
        <Notice kind="info">
          {t.withheld} <Link to={`/rounds/${roundId}`}>{t.backToRound}</Link>
        </Notice>
      ) : result.status === 'processing' ? (
        <Notice kind="info">{t.processing}</Notice>
      ) : (
        <FeedbackView report={report} actions={actions} />
      )}
    </>
  )
}
