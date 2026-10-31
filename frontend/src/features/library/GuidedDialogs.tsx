import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import { type Job, EXTRACT_KIND, GENERATE_KIND } from './api'
import { libraryStrings } from './strings'
import { TopicDialog } from './TopicDialog'
import type { EndedJob } from './useLiveJobs'

const c = libraryStrings.chapters
const SHOWN_KEY = 'aoc.guidedJobs'
const SHOWN_KEPT = 100

/** Job ids whose dialog this browser has shown already. */
function readShown(): number[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SHOWN_KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is number => typeof id === 'number') : []
  } catch {
    return []
  }
}

function markShown(id: number): void {
  try {
    const shown = readShown().filter((item) => item !== id)
    localStorage.setItem(SHOWN_KEY, JSON.stringify([...shown, id].slice(-SHOWN_KEPT)))
  } catch {
    // storage unavailable: the dialog may show again in another tab or after a reload
  }
}

/** A finished extraction offers questions; finished (or partly finished) generation offers a round. */
export function offersDialog(job: Job): boolean {
  if (job.kind === EXTRACT_KIND) return job.status === 'done'
  if (job.kind === GENERATE_KIND) return job.status === 'done' || job.status === 'partial'
  return false
}

const chapterQuery = (job: Job) => (job.chapter_id == null ? '' : `chapter=${job.chapter_id}`)

/** Where "Generate questions" and "Generate more" lead: the subject page's Generate panel. */
export function generateHref(job: Job, book: number | null): string {
  const query = [chapterQuery(job), `generate=${book ?? 'all'}`].filter(Boolean).join('&')
  return `/library/${job.subject_id}?${query}`
}

export function practiceHref(job: Job): string {
  const chapter = chapterQuery(job)
  return `/practice?subject=${job.subject_id}${chapter ? `&${chapter}` : ''}&sources=all`
}

/**
 * The next step after a task ends while the learner watches: once per job and browser, only for
 * jobs seen live on this page (never one that ended before), never on round or recording screens.
 */
export function GuidedDialogs({ ended, hidden }: { ended: EndedJob[]; hidden: boolean }) {
  const navigate = useNavigate()
  const [queue, setQueue] = useState<Job[]>([])
  const [known, setKnown] = useState<number[]>([])
  const [seen, setSeen] = useState(ended)
  if (seen !== ended) {
    setSeen(ended)
    const shown = readShown()
    const fresh = ended
      .map((row) => row.job)
      .filter((job) => offersDialog(job) && !known.includes(job.id) && !shown.includes(job.id))
    if (fresh.length > 0) {
      setKnown([...known, ...fresh.map((job) => job.id)])
      setQueue([...queue, ...fresh])
    }
  }
  const current = hidden ? null : (queue[0] ?? null)
  const currentId = current?.id ?? null
  useEffect(() => {
    if (currentId !== null) markShown(currentId)
  }, [currentId])

  if (!current) return null
  const close = () => setQueue((was) => was.slice(1))
  const go = (to: string) => {
    close()
    navigate(to)
  }
  const extract = current.kind === EXTRACT_KIND
  const title = current.document_title ?? current.subject_name
  return createPortal(
    <TopicDialog key={current.id} titleId="guided-title" onCancel={close}>
      {extract ? (
        <>
          <h2 id="guided-title">
            {c.topicsReady.before}
            <span className="content">
              {plNbsp(title)}
            </span>
            {c.topicsReady.after}
          </h2>
          <p className="small muted">{c.topicsReadyLead}</p>
          <div className="actions">
            <button className="btn" type="button" onClick={close}>
              {c.notNow}
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => go(generateHref(current, current.document_version_id))}
            >
              {c.generateQuestions}
            </button>
          </div>
        </>
      ) : (
        <>
          <h2 id="guided-title">{c.questionsReady}</h2>
          <p className="content">
            {plNbsp(title)}
          </p>
          <p className="small muted">{c.questionsReadyLead}</p>
          <div className="actions">
            <button className="btn" type="button" onClick={close}>
              {c.notNow}
            </button>
            <button className="btn" type="button" onClick={() => go(generateHref(current, null))}>
              {c.generateMore}
            </button>
            <button className="btn primary" type="button" onClick={() => go(practiceHref(current))}>
              {c.startRound}
            </button>
          </div>
        </>
      )}
    </TopicDialog>,
    document.body,
  )
}
