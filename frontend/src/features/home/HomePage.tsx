import { MonthBox } from '../costs/MonthBox'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { fetchProvidersStatus, hasGenerationKey } from '../../api/providers'
import { appStrings } from '../../i18n/app'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import { type Subject, listSubjects } from '../library/api'
import { act, getRound } from '../rounds/api'
import { listRounds } from './api'
import {
  type RecentRound,
  type SubjectRounds,
  formatWhen,
  isUnfinished,
  lastPractised,
  mergeRecent,
  roundHref,
  unfinished,
} from './recent'
import { homeStrings as s } from './strings'

const SUBJECTS_SHOWN = 20

interface Loaded {
  hasKey: boolean
  subjects: SubjectRounds[]
  full: Subject[]
}

async function load(): Promise<Loaded> {
  const [providers, subjects] = await Promise.all([fetchProvidersStatus(), listSubjects()])
  const hasKey = hasGenerationKey(providers)
  if (!hasKey) return { hasKey, subjects: [], full: subjects }
  const perSubject = await Promise.all(
    subjects.slice(0, SUBJECTS_SHOWN).map(async (subject) => ({
      subject: { id: subject.id, name: subject.name },
      rounds: await listRounds(subject.id).catch(() => []),
    })),
  )
  return { hasKey, subjects: perSubject, full: subjects }
}

function FirstRun() {
  return (
    <>
      <h1>{s.welcome}</h1>
      <p className="lede">{s.lede}</p>
      <section className="section" aria-labelledby="start-h">
        <h2 id="start-h">{s.getStarted}</h2>
        <ol className="rows step-list">
          <li>
            <span className="step-n" aria-current="step">
              1
            </span>
            <div>
              <h3>
                {s.addKey} <span className="chip warn">{s.notDone}</span>
              </h3>
              <p>{s.addKeyHelp}</p>
            </div>
            <div className="actions">
              <Link className="btn primary" to="/settings#keys">
                {s.addKey}
              </Link>
            </div>
          </li>
          <li>
            <span className="step-n">2</span>
            <div>
              <h3>
                {s.chooseVoice} <span className="chip">{s.optional}</span>
              </h3>
              <p>{s.chooseVoiceHelp}</p>
            </div>
            <div className="actions">
              <Link className="btn" to="/settings#languages">
                {s.chooseVoice}
              </Link>
            </div>
          </li>
          <li>
            <span className="step-n">3</span>
            <div>
              <h3>{s.checkMic}</h3>
              <p>{s.checkMicHelp}</p>
            </div>
            <div className="actions">
              <Link className="btn" to="/microphone">
                {s.checkMicButton}
              </Link>
            </div>
          </li>
          <li>
            <span className="step-n">4</span>
            <div>
              <h3>{s.addMaterials}</h3>
              <p>{s.addMaterialsHelp}</p>
            </div>
            <div className="actions">
              <Link className="btn" to="/library">
                {s.createSubject}
              </Link>
              <Link className="btn" to="/quick">
                {s.quickPractice}
              </Link>
            </div>
          </li>
        </ol>
      </section>
      <section className="section box tint" aria-labelledby="data-h">
        <h2 id="data-h">{s.yourData}</h2>
        <p className="lead mt-3">{s.yourDataBody}</p>
        <p className="mt-4">
          <Link to="/settings#privacy">{s.privacyDetails}</Link>
        </p>
      </section>
    </>
  )
}

function UnfinishedNotice({ round, onEnd }: { round: RecentRound; onEnd: () => void }) {
  return (
    <Notice kind="warn" className="mb-8">
      <strong>{s.unfinished}</strong>{' '}
      <span className="content">
        „{plNbsp(round.subjectName)}”
      </span>
      {s.unfinishedBody(Math.min(round.done + 1, round.total), round.total, formatWhen(round.started_at))}
      <div className="actions">
        <Link className="btn" to={`/rounds/${round.id}`}>
          {s.resume}
        </Link>
        <button className="btn" type="button" onClick={onEnd}>
          {s.endRound}
        </button>
      </div>
    </Notice>
  )
}

export function HomePage() {
  const navigate = useNavigate()
  const [serverDown, setServerDown] = useState(false)
  const [data, setData] = useState<Loaded | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [endError, setEndError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiGet('/health').catch(() => !cancelled && setServerDown(true))
    load()
      .then((loaded) => !cancelled && setData(loaded))
      .catch((failure: Error) => !cancelled && setLoadError(failure.message))
    return () => {
      cancelled = true
    }
  }, [])

  async function endRound(round: RecentRound) {
    setEndError(null)
    try {
      const state = await getRound(round.id)
      await act(round.id, {
        action: 'abandon',
        from_phase: state.phase,
        attempt_id: state.current_attempt?.id,
      })
      navigate(`/rounds/${round.id}/summary`)
    } catch (failure) {
      setEndError((failure as Error).message)
    }
  }

  const serverNotice = serverDown && (
    <Notice kind="error" className="mb-8">
      {appStrings.health.error}
    </Notice>
  )

  // Nothing but the loading line until the key is known: first-run users never see the header.
  if (!data)
    return (
      <>
        {serverNotice}
        {/* The server notice already says when it is unreachable. */}
        {!serverDown &&
          (loadError ? (
            <Notice kind="error">
              <p>{loadError}</p>
            </Notice>
          ) : (
            <p className="muted">{s.loading}</p>
          ))}
      </>
    )

  if (!data.hasKey)
    return (
      <>
        {serverNotice}
        <FirstRun />
      </>
    )

  const recent = mergeRecent(data.subjects)
  const open = unfinished(data.subjects)
  const rounds = new Map(data.subjects.map((item) => [item.subject.id, item.rounds]))

  return (
    <>
      <div className="page-head">
        <h1>{s.title}</h1>
        <div className="actions">
          <Link className="btn" to="/quick">
            {s.quickFromFile}
          </Link>
          <Link className="btn primary" to="/practice">
            {s.startRound}
          </Link>
        </div>
      </div>
      {serverNotice}
      {open && <UnfinishedNotice round={open} onEnd={() => void endRound(open)} />}
      {endError && (
        <Notice kind="error" className="mb-8">
          {endError}
        </Notice>
      )}
      <div className="cols-main section mt-10">
        <div>
          <section aria-labelledby="subjects-h">
            <div className="section-head">
              <h2 id="subjects-h">{s.subjects}</h2>
              <Link to="/library">{s.allSubjects}</Link>
            </div>
            {data.full.length === 0 ? (
              <p className="muted">{s.noSubjects}</p>
            ) : (
              <ul className="rows">
                {data.full.slice(0, SUBJECTS_SHOWN).map((subject) => {
                  const day = lastPractised(rounds.get(subject.id) ?? [])
                  return (
                    <li key={subject.id}>
                      <div>
                        <Link className="title content" to={`/library/${subject.id}`}>
                          {plNbsp(subject.name)}
                        </Link>
                        <div className="meta">
                          <span>{s.documents(subject.document_count)}</span>
                          {day && <span>{s.lastPractised(day)}</span>}
                        </div>
                      </div>
                      {subject.document_count > 0 ? (
                        <Link className="btn small" to={`/practice?subject=${subject.id}`}>
                          {s.practise}
                        </Link>
                      ) : (
                        <Link className="btn small" to={`/library/${subject.id}`}>
                          {s.open}
                        </Link>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
          <section className="section" aria-labelledby="rounds-h">
            <div className="section-head">
              <h2 id="rounds-h">{s.recent}</h2>
              <Link to="/progress/history">{s.history}</Link>
            </div>
            {recent.length === 0 ? (
              <p className="muted">{s.noRounds}</p>
            ) : (
              <ul className="rows rounds in-col">
                {recent.map((round) => (
                  <li key={round.id}>
                    <div>
                      <div className="head">
                        <Link className="title num" to={roundHref(round)}>
                          {formatWhen(round.started_at)}
                        </Link>
                        <span className="subject content">
                          {plNbsp(round.subjectName)}
                        </span>
                      </div>
                      <div className="meta">
                        <span>{s.roundKind(round.feedback_timing, 'round_type' in round ? String(round.round_type) : undefined)}</span>
                        <span className="num">{s.questions(round.total)}</span>
                        {isUnfinished(round) && <span className="chip warn">{s.unfinishedChip}</span>}
                        {round.status === 'abandoned' && (
                          <span className="chip">{s.endedEarlyChip}</span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        <aside className="side" aria-label={s.nextSteps}>
          <section aria-labelledby="quick-h">
            <h2 id="quick-h">{s.quickPractice}</h2>
            <p>{s.quickHelp}</p>
            <Link className="btn mt-4" to="/quick">
              {s.quickFromFile}
            </Link>
          </section>
          <MonthBox />
        </aside>
      </div>
    </>
  )
}
