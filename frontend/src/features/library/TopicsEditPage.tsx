import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  type Chapter,
  type SplitPart,
  type Subject,
  type Term,
  type Topic,
  flattenTopics,
  getSubject,
  listChapters,
  listTerms,
  listTopics,
  mergeTopics,
  removeTopic,
  renameTopic,
  splitTopic,
} from './api'
import { ChapterTabs } from './ChapterTabs'
import { isChaptered } from './chapters'
import { MergeDialog } from './MergeDialog'
import { RemoveDialog } from './RemoveDialog'
import { SplitDialog } from './SplitDialog'
import { libraryStrings } from './strings'
import { Notice, NoteNotice, type Note } from '../../ui/Notice'
import { ContentLanguageHost, useContentLanguage } from '../language/ContentLanguage'

const s = libraryStrings.topicEdit
const SAVE_DELAY_MS = 800
const FIRST_SHOWN = 20

type Dialog =
  | { kind: 'remove'; topic: Topic }
  | { kind: 'merge'; topics: Topic[] }
  | { kind: 'split'; topic: Topic }
  | null

export function TopicsEditPage() {
  return <ContentLanguageHost>{(setCode) => <TopicsEdit onLanguage={setCode} />}</ContentLanguageHost>
}

function TopicsEdit({ onLanguage }: { onLanguage: (code: string | null) => void }) {
  const cl = useContentLanguage()
  const subjectId = Number(useParams().subjectId)
  const [searchParams] = useSearchParams()
  const [subject, setSubject] = useState<Subject | null>(null)
  const language = subject?.language ?? null
  useLayoutEffect(() => onLanguage(language), [onLanguage, language])
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  const [topics, setTopics] = useState<Topic[]>([])
  const [terms, setTerms] = useState<Term[]>([])
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [ticked, setTicked] = useState<number[]>([])
  const [shown, setShown] = useState(FIRST_SHOWN)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<Note | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timers = useRef<Record<number, number>>({})
  // The latest typed names, so leaving the page can still save the ones waiting for their timer.
  const latest = useRef<Record<number, string>>({})
  const current = useRef<Topic[]>([])

  // with one chapter, nothing asks or sends a chapter
  const chaptered = isChaptered(subject) && chapters.length >= 2
  const wanted = Number(searchParams.get('chapter'))
  const chapterId = chaptered ? (chapters.find((chapter) => chapter.id === wanted) ?? chapters[0]).id : null
  const chapterRef = useRef(chapterId)
  useEffect(() => {
    chapterRef.current = chapterId
  })

  const apply = useCallback((tree: Topic[]) => {
    const flat = flattenTopics(tree)
    current.current = flat
    setTopics(flat)
    setDrafts({})
    setTicked((was) => was.filter((id) => flat.some((topic) => topic.id === id)))
  }, [])

  const reload = useCallback(async () => {
    const [tree, nextTerms] = await Promise.all([listTopics(subjectId, chapterRef.current), listTerms(subjectId)])
    apply(tree)
    setTerms(nextTerms)
  }, [subjectId, apply])

  useEffect(() => {
    let cancelled = false
    getSubject(subjectId)
      .then(async (nextSubject) => {
        const nextChapters = isChaptered(nextSubject) ? await listChapters(subjectId) : []
        if (cancelled) return
        setChapters(nextChapters)
        setSubject(nextSubject)
      })
      .catch((failure: Error) => !cancelled && setError(failure.message))
    const pending = timers.current
    const typed = latest.current
    return () => {
      cancelled = true
      // A name typed just before leaving is saved now instead of being dropped with its timer.
      Object.entries(pending).forEach(([id, timer]) => {
        window.clearTimeout(timer)
        delete pending[Number(id)]
        const title = typed[Number(id)]?.trim()
        const stored = current.current.find((topic) => topic.id === Number(id))
        if (stored && title && title !== stored.title) void renameTopic(stored.id, title).catch(() => undefined)
      })
    }
  }, [subjectId])

  const ready = subject !== null && subject.id === subjectId
  const key = `${subjectId}:${chapterId}`
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    Promise.all([listTopics(subjectId, chapterId), listTerms(subjectId)])
      .then(([tree, nextTerms]) => {
        if (cancelled) return
        apply(tree)
        setTerms(nextTerms)
        setLoadedFor(`${subjectId}:${chapterId}`)
      })
      .catch((failure: Error) => !cancelled && setError(failure.message))
    return () => {
      cancelled = true
    }
  }, [ready, subjectId, chapterId, apply])

  async function save(id: number, text: string) {
    window.clearTimeout(timers.current[id])
    delete timers.current[id]
    const title = text.trim()
    const stored = current.current.find((topic) => topic.id === id)
    if (!stored || !title || title === stored.title) return
    try {
      await renameTopic(id, title)
      current.current = current.current.map((topic) =>
        topic.id === id ? { ...topic, title } : topic,
      )
      setTopics(current.current)
      setNote(null)
    } catch (failure) {
      setNote({ text: (failure as Error).message, error: true })
    }
  }

  function type(id: number, text: string) {
    setDrafts((was) => ({ ...was, [id]: text }))
    latest.current[id] = text
    window.clearTimeout(timers.current[id])
    timers.current[id] = window.setTimeout(() => void save(id, text), SAVE_DELAY_MS)
  }

  function leave(id: number, text: string) {
    void save(id, text)
    if (!text.trim()) {
      const stored = current.current.find((topic) => topic.id === id)
      setDrafts((was) => ({ ...was, [id]: stored?.title ?? '' }))
    }
  }

  function toggle(id: number) {
    setTicked((was) => (was.includes(id) ? was.filter((item) => item !== id) : [...was, id]))
  }

  async function run(action: () => Promise<string>) {
    setBusy(true)
    try {
      const message = await action()
      await reload()
      setDialog(null)
      setNote({ text: message, error: false })
    } catch (failure) {
      setDialog(null)
      setNote({ text: (failure as Error).message, error: true })
    } finally {
      setBusy(false)
    }
  }

  const confirmRemove = (topic: Topic) =>
    run(async () => {
      const result = await removeTopic(topic.id)
      return s.removed(topic.title, result.questions_retired)
    })
  const confirmMerge = (ids: number[], title: string) =>
    run(async () => {
      const merged = await mergeTopics(ids, title)
      setTicked([])
      return s.merged(merged.title)
    })
  const confirmSplit = (topic: Topic, parts: SplitPart[]) =>
    run(async () => {
      const created = await splitTopic(topic.id, parts)
      return s.splitDone(created.length)
    })

  if (error)
    return (
      <Notice kind="error">
        <p>{error}</p>
      </Notice>
    )
  if (!subject || loadedFor === null) return <p className="muted">{s.loading}</p>
  const stale = loadedFor !== key

  const objectiveCount = topics.reduce((sum, topic) => sum + topic.objectives.length, 0)
  const visible = topics.slice(0, shown)
  const hidden = topics.length - visible.length
  const selectedTopics = topics.filter((topic) => ticked.includes(topic.id))

  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/library">{s.crumb}</Link> <span aria-hidden="true">›</span>{' '}
        <Link className="content" to={`/library/${subjectId}`}>
          {subject.name}
        </Link>{' '}
        <span aria-hidden="true">›</span> {s.title}
      </nav>
      <div className="page-head">
        <div>
          <h1>{s.title}</h1>
          <p>
            {s.summary(topics.length, objectiveCount)} {s.lede}
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            type="button"
            disabled={selectedTopics.length < 2}
            onClick={() => setDialog({ kind: 'merge', topics: selectedTopics })}
          >
            {s.merge} <span className="num">({selectedTopics.length})</span>
          </button>
          <Link className="btn primary" to={`/library/${subjectId}${chapterId === null ? '' : `?chapter=${chapterId}`}`}>
            {s.done}
          </Link>
        </div>
      </div>
      {chaptered && <ChapterTabs chapters={chapters} current={chapterId} />}
      <NoteNotice note={note} />
      {stale ? (
        <p className="muted">{s.loading}</p>
      ) : topics.length === 0 ? (
        <p className="muted">{s.none}</p>
      ) : (
        <ul className="rows zebra stack-sm topic-list" aria-label={s.list}>
          {visible.map((topic) => {
            const own = terms.filter((term) => term.topic_id === topic.id)
            return (
              <li key={topic.id}>
                <div className="topic-edit">
                  <input
                    type="checkbox"
                    checked={ticked.includes(topic.id)}
                    aria-label={s.select(topic.title)}
                    onChange={() => toggle(topic.id)}
                  />
                  <div>
                    <textarea
                      className="textarea content name-edit"
                      lang={cl.bcp47}
                      rows={1}
                      aria-label={s.name}
                      value={drafts[topic.id] ?? topic.title}
                      onChange={(event) => type(topic.id, event.target.value)}
                      onBlur={(event) => leave(topic.id, event.target.value)}
                    />
                    <p className="meta num">
                      <span>{s.objectives(topic.objectives.length)}</span>
                      <span>{s.questions(topic.question_count)}</span>
                      <span>{s.passages(topic.passage_count)}</span>
                    </p>
                    {own.length > 0 && (
                      <p className="terms">
                        {s.terms}:{' '}
                        <span className="content" lang={cl.bcp47}>
                          {own.map((term) => term.term).join(', ')}
                        </span>
                      </p>
                    )}
                    {topic.objectives.length > 0 && (
                      <ul className="keypoints" lang={cl.bcp47} aria-label={s.objectivesList}>
                        {topic.objectives.map((objective) => (
                          <li key={objective.id}>{cl.typeset(objective.text)}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
                <div className="end">
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => setDialog({ kind: 'split', topic })}
                  >
                    {s.split}
                  </button>
                  <button
                    className="btn small danger"
                    type="button"
                    onClick={() => setDialog({ kind: 'remove', topic })}
                  >
                    {s.remove}
                  </button>
                </div>
              </li>
            )
          })}
          {hidden > 0 && (
            <li className="more-row">
              <button className="btn link" type="button" onClick={() => setShown(topics.length)}>
                {s.more(hidden)}
              </button>
            </li>
          )}
        </ul>
      )}
      <div className="list-foot">
        <span className="small muted">{s.hint}</span>
      </div>
      {dialog?.kind === 'remove' && (
        <RemoveDialog
          topic={dialog.topic}
          busy={busy}
          onCancel={() => setDialog(null)}
          onConfirm={() => void confirmRemove(dialog.topic)}
        />
      )}
      {dialog?.kind === 'merge' && (
        <MergeDialog
          topics={dialog.topics}
          busy={busy}
          onCancel={() => setDialog(null)}
          onConfirm={(title) =>
            void confirmMerge(
              dialog.topics.map((topic) => topic.id),
              title,
            )
          }
        />
      )}
      {dialog?.kind === 'split' && (
        <SplitDialog
          topic={dialog.topic}
          busy={busy}
          onCancel={() => setDialog(null)}
          onConfirm={(parts) => void confirmSplit(dialog.topic, parts)}
        />
      )}
    </>
  )
}
