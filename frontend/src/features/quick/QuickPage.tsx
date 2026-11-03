import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Notice, NoteNotice, type Note } from '../../ui/Notice'
import {
  type ContentLanguage,
  type Document,
  type Job,
  type Subject,
  type Term,
  type Topic,
  EXTRACT_KIND,
  GENERATE_KIND,
  flattenTopics,
  getSubject,
  listDocuments,
  listJobs,
  listLanguages,
  listTerms,
  listTopics,
  renameSubject,
  startExtraction,
} from '../library/api'
import { ContentLanguageHost } from '../language/ContentLanguage'
import { useJobs } from '../library/useJobs'
import { createRound, type QuestionAudio } from '../rounds/api'
import { prefetchRound } from '../rounds/questionAudio'
import { type Preferences, fetchPreferences } from '../settings/api'
import { settingsStrings } from '../settings/strings'
import { countActive, generateQuestions, generationBody } from './api'
import { QuickEstimate } from './QuickEstimate'
import { QuickHeader } from './QuickHeader'
import {
  type Step,
  MAX_QUESTIONS,
  NEED_PREMIUM_VOICE,
  countKnown,
  deriveStep,
  perTopic,
  questionCount,
  quickAudio,
  quickVoice,
  recall,
  remember,
  spoken,
  stageOf,
} from './quickState'
import { quickStrings as s } from './strings'
import { useContentLanguage } from '../language/ContentLanguage'

const TOPICS_POLL_MS = 1500

interface Loaded {
  subject: Subject
  document: Document | null
  topics: Topic[]
  terms: Term[]
  activeQuestions: number
  preferences: Preferences
  jobs: Job[]
  /** The language list, for the voice of the subject's language; empty when it could not be read. */
  languages: ContentLanguage[]
}

async function load(subjectId: number): Promise<Loaded> {
  const [subject, documents, tree, terms, activeQuestions, preferences, jobs, languages] = await Promise.all([
    getSubject(subjectId),
    listDocuments(subjectId),
    listTopics(subjectId),
    listTerms(subjectId),
    countActive(subjectId),
    fetchPreferences(),
    listJobs(subjectId),
    listLanguages().catch(() => [] as ContentLanguage[]),
  ])
  return {
    subject,
    document: documents[0] ?? null,
    topics: flattenTopics(tree),
    terms,
    activeQuestions,
    preferences,
    jobs,
    languages,
  }
}

/** The engine that reads the questions of this subject (null: the language's default voice). */
function voiceOf(data: Loaded): string | null {
  const code = data.subject.language ?? 'pl'
  return quickVoice(data.preferences, code, data.languages.find((row) => row.code === code))
}

function Meter({ done, total, label }: { done: number; total: number; label: string }) {
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <div
      className="meter-bar"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-label={label}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  )
}

export function QuickPage() {
  return <ContentLanguageHost>{(setCode) => <QuickView onLanguage={setCode} />}</ContentLanguageHost>
}

function QuickView({ onLanguage }: { onLanguage: (code: string | null) => void }) {
  const cl = useContentLanguage()
  const subjectId = Number(useParams().subjectId)
  const navigate = useNavigate()
  const [data, setData] = useState<Loaded | null>(null)
  const language = data?.subject.language ?? null
  useLayoutEffect(() => onLanguage(language), [onLanguage, language])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [showTopics, setShowTopics] = useState(false)
  const [ticked, setTicked] = useState<number[] | null>(null)
  const [count, setCount] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [name, setName] = useState<string | null>(null)
  const [nameNote, setNameNote] = useState<Note | null>(null)
  const extractionTried = useRef(false)

  const reload = useCallback(async () => {
    setData(await load(subjectId))
  }, [subjectId])

  const { jobs, attach, resume } = useJobs(subjectId, () => {
    reload().catch(() => undefined)
  })

  useEffect(() => {
    let cancelled = false
    load(subjectId)
      .then((loaded) => !cancelled && setData(loaded))
      .catch((failure: Error) => !cancelled && setLoadError(failure.message))
    return () => {
      cancelled = true
    }
  }, [subjectId])

  const version = data?.document?.latest_version ?? null
  const topics = useMemo(() => data?.topics ?? [], [data])
  // Jobs seen by this page; the ones read with the page stand in until the poller has its own.
  const knownJobs = jobs.length > 0 ? jobs : (data?.jobs ?? [])
  const step: Step | null = data
    ? deriveStep({
        version,
        jobs: knownJobs,
        topics,
        // A task that just ended is waited on until the page data read after it comes in.
        activeQuestions: countKnown(knownJobs, data.jobs) ? data.activeQuestions : null,
        showTopics,
      })
    : null

  // Extraction starts once by itself; a 409 means a task already runs, so the state is reloaded.
  useEffect(() => {
    if (step !== 'extract-start' || !version || extractionTried.current) return
    if (data?.jobs.some((job) => job.kind === EXTRACT_KIND)) return
    extractionTried.current = true
    startExtraction(version.id)
      .then((started) => attach(started.job_id))
      .catch((failure: Error) => {
        if (failure instanceof ApiError && failure.status === 409) {
          reload().catch(() => undefined)
        } else {
          setActionError(failure.message)
        }
      })
  }, [step, version, data, attach, reload])

  // While questions are prepared, the topics show which already have some.
  useEffect(() => {
    if (step !== 'generating') return
    const timer = window.setInterval(() => {
      reload().catch(() => undefined)
    }, TOPICS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [step, reload])

  const topicIds = topics.map((topic) => topic.id)
  const chosen = ticked ?? topicIds
  const countText = count ?? String(data?.preferences.round_question_count ?? '')
  const total = questionCount(countText)

  async function prepare() {
    if (!data || total === null) return
    setActionError(null)
    try {
      const started = await generateQuestions(subjectId, chosen, perTopic(total, chosen.length))
      remember(subjectId, { count: total, topicIds: chosen })
      // The new task is known before the topics close, so the previous one's result never shows.
      await attach(started.job_id)
      setShowTopics(false)
      await reload()
    } catch (failure) {
      setActionError((failure as Error).message)
    }
  }

  async function start() {
    if (!data) return
    const { preferences } = data
    const voice = voiceOf(data)
    const withQuestions = topics.filter((topic) => topic.question_count > 0).map((t) => t.id)
    setStarting(true)
    setWarning(null)
    try {
      const ask = (audio: QuestionAudio) =>
        createRound({
          subject_id: subjectId,
          config: {
            round_type: 'standard',
            scope: { kind: 'topics', topic_ids: withQuestions, document_ids: [] },
            source_policy: 'materials',
            feedback_timing: 'practice',
            question_count: Math.min(preferences.round_question_count, data.activeQuestions),
            style: 'mixed',
            difficulty: 'mixed',
            prep_seconds: preferences.round_prep_seconds,
            answer_seconds: preferences.round_answer_seconds,
            question_audio: audio,
          },
        })
      let round
      try {
        round = await ask(quickAudio(voice))
      } catch (failure) {
        // a language without a premium voice yet: the browser voice reads the questions
        const noPremium = failure instanceof ApiError && failure.status === 422 && failure.message === NEED_PREMIUM_VOICE
        if (voice !== null || !noPremium) throw failure
        round = await ask({ mode: 'browser' })
      }
      prefetchRound(round) // the first question's voice is made while the microphone is checked
      navigate(`/practice/${round.id}/microphone`)
    } catch (failure) {
      setWarning((failure as Error).message)
      setStarting(false)
    }
  }

  async function saveName() {
    if (!data) return
    try {
      const saved = await renameSubject(subjectId, (name ?? data.subject.name).trim())
      setData({ ...data, subject: saved })
      setName(null)
      setNameNote({ text: s.ready.nameSaved, error: false })
    } catch (failure) {
      setNameNote({ text: (failure as Error).message, error: true })
    }
  }

  if (loadError)
    return (
      <>
        <QuickHeader stage={0} />
        <Notice kind="error">
          <p>{loadError}</p>
        </Notice>
        <div className="actions spaced">
          <Link className="btn" to="/quick">
            {s.back}
          </Link>
        </div>
      </>
    )
  if (!data || !step) return <p className="muted">{s.loading}</p>

  const filename = version?.filename ?? data.subject.name
  const voice = voiceOf(data)
  const voiceLabel = (voice !== null && s.ready.audioValue[voice]) || s.ready.audioDefault
  const extractJob = knownJobs.filter((job) => job.kind === EXTRACT_KIND).sort((a, b) => b.id - a.id)[0]
  const generateJob = knownJobs
    .filter((job) => job.kind === GENERATE_KIND)
    .sort((a, b) => b.id - a.id)[0]
  const failedJob = step === 'extract-failed' ? extractJob : generateJob
  const remembered = recall(subjectId)

  return (
    <>
      <QuickHeader stage={stageOf(step)} />
      {actionError && (
        <Notice kind="error" className="mb-8">
          <p>{actionError}</p>
        </Notice>
      )}

      {(step === 'extract-start' || step === 'finding') && (
        <div className="cols-main">
          <section aria-labelledby="find-h" role="status">
            <h2 id="find-h">{s.finding.heading}</h2>
            <Meter
              done={extractJob ? extractJob.steps_done : 0}
              total={extractJob ? extractJob.steps_total : 0}
              label={s.finding.progress}
            />
            <p className="mt-4">
              {s.finding.reading}{' '}
              <span className="file-name">
                „{filename}”
              </span>
              . {s.finding.leave}
            </p>
            <div className="actions spaced">
              <Link className="btn" to="/">
                {s.generating.home}
              </Link>
            </div>
          </section>
        </div>
      )}

      {(step === 'extract-failed' || step === 'generate-failed') && (
        <section aria-labelledby="fail-h">
          <h2 id="fail-h">{step === 'extract-failed' ? s.failed.extract : s.failed.generate}</h2>
          <Notice kind="error">
            <p>
              {(step === 'extract-failed' && version?.status === 'failed' ? version.message : null) ??
                failedJob?.error_message ??
                s.failed.unknown}
            </p>
          </Notice>
          <div className="actions spaced">
            {failedJob?.resumable && (
              <button
                className="btn primary"
                type="button"
                onClick={() => failedJob && void resume(failedJob.id).catch((e: Error) => setActionError(e.message))}
              >
                {s.failed.resume}
              </button>
            )}
            <Link className={failedJob?.resumable ? 'btn' : 'btn primary'} to="/quick">
              {s.back}
            </Link>
            {step === 'generate-failed' && !failedJob?.resumable && (
              <button className="btn" type="button" onClick={() => setShowTopics(true)}>
                {s.failed.topics}
              </button>
            )}
          </div>
        </section>
      )}

      {step === 'topics' && (
        <div className="cols-main">
          <section aria-labelledby="top-h">
            <h2 id="top-h">
              {s.topics.heading}{' '}
              <span className="file-name">
                „{filename}”
              </span>
            </h2>
            {version && (
              <p className="meta muted small num mt-2">
                {s.topics.meta(version.page_count, version.char_count, version.passage_count)}
              </p>
            )}
            {topics.length === 0 ? (
              <p className="muted mt-4">{s.topics.none}</p>
            ) : (
              <>
                <p className="lead mt-4">{s.topics.lead}</p>
                <ul className="rows topics" aria-label={s.topics.label}>
                  {topics.map((topic) => (
                    <li key={topic.id}>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={chosen.includes(topic.id)}
                          onChange={() =>
                            setTicked(
                              chosen.includes(topic.id)
                                ? chosen.filter((id) => id !== topic.id)
                                : [...chosen, topic.id],
                            )
                          }
                        />{' '}
                        <span className="content" lang={cl.bcp47}>
                          {cl.typeset(topic.title)}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {data.terms.length > 0 && (
              <>
                <h3 className="mt-8">{s.topics.terms}</h3>
                <ul className="band-list mt-3" aria-label={s.topics.terms}>
                  {data.terms.slice(0, 12).map((term) => (
                    <li key={term.term} className="chip content" lang={cl.bcp47}>
                      {term.term}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="actions spaced">
              <button
                className="btn primary"
                type="button"
                disabled={chosen.length === 0 || total === null}
                onClick={() => void prepare()}
              >
                {s.topics.prepare}
              </button>
              <Link className="btn" to={`/library/${subjectId}`}>
                {s.topics.check}
              </Link>
              <Link className="btn" to="/quick">
                {s.back}
              </Link>
            </div>
          </section>

          <aside className="side" aria-label={s.topics.side}>
            <section className="box tint" aria-labelledby="q-h">
              <h2 id="q-h">{s.topics.side}</h2>
              <div className="field">
                <label className="label" htmlFor="quick-count">
                  {s.topics.questions}
                </label>
                <input
                  className="input num"
                  id="quick-count"
                  type="number"
                  min={1}
                  max={MAX_QUESTIONS}
                  value={countText}
                  aria-invalid={total === null}
                  aria-describedby={total === null ? 'quick-count-error' : undefined}
                  onChange={(event) => setCount(event.target.value)}
                />
                {total === null && (
                  <p className="error-text" id="quick-count-error">
                    {s.topics.countError(MAX_QUESTIONS)}
                  </p>
                )}
              </div>
              <QuickEstimate
                body={
                  total !== null && chosen.length > 0
                    ? generationBody(subjectId, chosen, perTopic(total, chosen.length))
                    : null
                }
                uses={s.topics.uses(settingsStrings.keys.providers[data.preferences.generation_provider])}
              />
              <p className="small muted mt-3">
                {s.topics.library}{' '}
                <Link to={`/library/${subjectId}/topics`}>{s.topics.libraryLink}</Link>{' '}
                {s.topics.libraryAfter}
              </p>
            </section>
          </aside>
        </div>
      )}

      {step === 'generating' && (
        <div className="cols-main">
          <section aria-labelledby="gen-h" role="status">
            <h2 id="gen-h">{s.generating.heading(remembered?.count ?? null)}</h2>
            <Meter
              done={generateJob?.steps_done ?? 0}
              total={generateJob?.steps_total ?? 0}
              label={s.generating.progress}
            />
            <p className="lead mt-4">{s.generating.help}</p>
            <div className="actions spaced">
              <Link className="btn" to="/">
                {s.generating.home}
              </Link>
            </div>
            <p className="small muted mt-4">{s.generating.moves}</p>
          </section>
          <aside className="side" aria-label={s.generating.topicsSide}>
            <section aria-labelledby="t-h">
              <h2 id="t-h">{s.generating.topics}</h2>
              <GeneratingTopics
                topics={topics.filter((topic) => !remembered || remembered.topicIds.includes(topic.id))}
              />
            </section>
          </aside>
        </div>
      )}

      {(step === 'ready' || step === 'empty') && (
        <div className="cols-main">
          {step === 'ready' ? (
            <section aria-labelledby="ready-h">
              <Notice kind="ok">
                <strong id="ready-h">{s.ready.heading(data.activeQuestions)}</strong>
                {s.ready.from(topics.filter((t) => t.question_count > 0).length)}{' '}
                <span className="file-name">
                  {filename}
                </span>
                {s.ready.checked}
              </Notice>
              {warning && (
                <Notice kind="warn" className="mt-4">
                  <p>{warning}</p>
                </Notice>
              )}
              <h2 className="mt-8">{s.ready.settings}</h2>
              <dl className="facts mt-4">
                <dt>{s.ready.feedback}</dt>
                <dd>{s.ready.feedbackValue}</dd>
                <dt>{s.ready.time}</dt>
                <dd className="num">
                  {s.ready.timeValue(
                    data.preferences.round_prep_seconds > 0
                      ? spoken(data.preferences.round_prep_seconds)
                      : null,
                    spoken(data.preferences.round_answer_seconds),
                  )}
                </dd>
                <dt>{s.ready.audio}</dt>
                <dd>{voiceLabel}</dd>
              </dl>
              <div className="actions spaced">
                <button
                  className="btn primary big"
                  type="button"
                  disabled={starting}
                  onClick={() => void start()}
                >
                  {s.ready.start}
                </button>
                <Link className="btn" to={`/practice?subject=${subjectId}`}>
                  {s.ready.change}
                </Link>
              </div>
            </section>
          ) : (
            <section aria-label={s.stages[3]}>
              <Notice kind="warn">
                <p>{s.empty.warning}</p>
              </Notice>
              <div className="actions spaced">
                <button className="btn primary" type="button" onClick={() => setShowTopics(true)}>
                  {s.empty.back}
                </button>
              </div>
            </section>
          )}

          <aside className="side" aria-label={s.ready.keepSide}>
            <section className="box tint" aria-labelledby="keep-h">
              <h2 id="keep-h">{s.ready.keepSide}</h2>
              <p className="small mb-4">{s.ready.keepNote}</p>
              <div className="field">
                <label className="label" htmlFor="quick-name">
                  {s.ready.name}
                </label>
                <input
                  className="input content"
                  id="quick-name"
                  type="text"
                  value={name ?? data.subject.name}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="actions mt-5">
                <button
                  className="btn"
                  type="button"
                  disabled={!(name ?? data.subject.name).trim()}
                  onClick={() => void saveName()}
                >
                  {s.ready.saveName}
                </button>
              </div>
              <NoteNotice note={nameNote} />
              <p className="small muted mt-4">
                {s.ready.existing} {s.ready.later} <span className="chip">{s.ready.laterChip}</span>
              </p>
            </section>
          </aside>
        </div>
      )}
    </>
  )
}

function GeneratingTopics({ topics }: { topics: Topic[] }) {
  const cl = useContentLanguage()
  const first = topics.findIndex((topic) => topic.question_count === 0)
  return (
    <ul className="rows compact">
      {topics.map((topic, index) => {
        const done = topic.question_count > 0
        const label = done ? s.generating.done : index === first ? s.generating.preparing : s.generating.waiting
        const chip = done ? 'chip ok' : index === first ? 'chip accent' : 'chip'
        return (
          <li key={topic.id}>
            <span className="content" lang={cl.bcp47}>
              {cl.typeset(topic.title)}
            </span>
            <span className={chip}>{label}</span>
          </li>
        )
      })}
    </ul>
  )
}
