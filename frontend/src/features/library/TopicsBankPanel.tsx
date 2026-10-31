import { EstimateLine } from '../costs/EstimateLine'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Notice } from '../../ui/Notice'
import { type Document, type GenerateOptions, type Term, type Topic } from './api'
import { defaultBookChoice, hasExtractedTopics } from './chapters'
import { libraryStrings } from './strings'
import { useContentLanguage } from '../language/ContentLanguage'

const t = libraryStrings.topics
const b = libraryStrings.bank
const src = libraryStrings.sources
const c = libraryStrings.chapters

const openKey = (subjectId: number) => `aoc.topicsOpen.${subjectId}`

function readOpen(subjectId: number): boolean {
  try {
    return localStorage.getItem(openKey(subjectId)) === '1'
  } catch {
    return false
  }
}

function writeOpen(subjectId: number, open: boolean): void {
  try {
    localStorage.setItem(openKey(subjectId), open ? '1' : '0')
  } catch {
    // storage unavailable: the list starts collapsed next time
  }
}

export function TopicsPanel({
  subjectId,
  topics,
  terms,
  chapterId = null,
}: {
  subjectId: number
  topics: Topic[]
  terms: Term[]
  /** The chapter shown, for a subject with two or more chapters. */
  chapterId?: number | null
}) {
  const cl = useContentLanguage()
  // collapsed by default; the choice is remembered per subject
  const [open, setOpen] = useState(() => readOpen(subjectId))
  const [openFor, setOpenFor] = useState(subjectId)
  if (openFor !== subjectId) {
    setOpenFor(subjectId)
    setOpen(readOpen(subjectId))
  }
  const toggle = () => {
    writeOpen(subjectId, !open)
    setOpen(!open)
  }
  return (
    <section className="section" id="topics" aria-labelledby="topics-title">
      <div className="section-head">
        <h2 id="topics-title">{t.title}</h2>
        {topics.length > 0 && (
          <>
            <span className="muted small">{t.count(topics.length)}</span>
            <Link
              className="btn small"
              to={`/library/${subjectId}/topics${chapterId === null ? '' : `?chapter=${chapterId}`}`}
            >
              {t.edit}
            </Link>
            <button className="btn small" type="button" aria-expanded={open} aria-controls="topics-list" onClick={toggle}>
              {open ? t.hide : t.show}
            </button>
          </>
        )}
      </div>
      {topics.length === 0 ? (
        <p className="muted">{t.none}</p>
      ) : (
        <ul className="rows compact" id="topics-list" hidden={!open}>
          {topics.map((topic) => {
            const own = terms.filter((term) => term.topic_id === topic.id)
            return (
              <li key={topic.id}>
                <div>
                  <span className="content" lang={cl.bcp47}>
                    {cl.typeset(topic.title)}
                  </span>
                  {topic.description && (
                    <p className="desc content small" lang={cl.bcp47}>
                      {cl.typeset(topic.description)}
                    </p>
                  )}
                  {own.length > 0 && (
                    <p className="meta">
                      <span>
                        {t.terms}:{' '}
                        <span className="content" lang={cl.bcp47}>
                          {own.map((term) => term.term).join(', ')}
                        </span>
                      </span>
                    </p>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

type GenerationPolicy = NonNullable<GenerateOptions['source_policy']>
const POLICIES: GenerationPolicy[] = ['materials', 'expanded', 'outside']

/** Active topics and their active children, as generation covers them. */
function activeTopics(topics: Topic[]): Topic[] {
  return topics
    .filter((topic) => topic.status === 'active')
    .flatMap((topic) => [topic, ...(topic.children ?? []).filter((child) => child.status === 'active')])
}

/** The topics without a question under the policy (active or needs review). */
export function topicsWithoutQuestions(topics: Topic[], policy: GenerationPolicy): Topic[] {
  return activeTopics(topics).filter((topic) => (topic.question_counts?.[policy] ?? 0) === 0)
}

export function BankPanel({
  subjectId,
  topics,
  hasTopics,
  questionCount,
  canRun,
  busy,
  onGenerate,
  documents = [],
  chapterId = null,
  preset = null,
  reviewCount = 0,
}: {
  subjectId: number
  topics: Topic[]
  hasTopics: boolean
  questionCount: number
  canRun: boolean
  busy: boolean
  onGenerate: (options: GenerateOptions) => void
  /** The documents of the chapter shown (all of them with one chapter): the books "Generate for" offers. */
  documents?: Document[]
  /** Sent with every request, only for a subject with two or more chapters. */
  chapterId?: number | null
  /** A version (or all topics) to preselect, e.g. from "Generate questions" on a document row. */
  preset?: number | 'all' | null
  /** Questions in review after a document was replaced (SubjectOut.source_review_count). */
  reviewCount?: number
}) {
  const [perTopic, setPerTopic] = useState('2')
  const [style, setStyle] = useState('mixed')
  const [difficulty, setDifficulty] = useState('mixed')
  const [sourcePolicy, setSourcePolicy] = useState<GenerationPolicy>('materials')
  /** null until the learner picks: "only without questions" whenever there are such topics. */
  const [onlyEmpty, setOnlyEmpty] = useState<boolean | null>(null)
  /** `book:<version id>` or `all`; null until the learner picks: the book awaiting questions, else all. */
  const presetKey = preset === null ? null : preset === 'all' ? 'all' : `book:${preset}`
  const [choice, setChoice] = useState<string | null>(presetKey)
  const [presetFor, setPresetFor] = useState(presetKey)
  if (presetFor !== presetKey) {
    setPresetFor(presetKey)
    if (presetKey !== null) setChoice(presetKey)
  }
  const count = Number(perTopic)
  const valid = Number.isInteger(count) && count >= 1 && count <= 10
  const all = activeTopics(topics)
  const empty = topicsWithoutQuestions(topics, sourcePolicy)
  const books = documents.filter(hasExtractedTopics)
  const offered = (key: string | null) =>
    key === 'all' || books.some((doc) => key === `book:${doc.latest_version.id}`)
  const fallback = defaultBookChoice(documents, null)
  const chosen = offered(choice) ? (choice as string) : fallback ? `book:${fallback.latest_version.id}` : 'all'
  const book = chosen === 'all' ? null : Number(chosen.slice('book:'.length))
  const emptyOnly = empty.length > 0 && (onlyEmpty ?? true)
  // topic_ids only for a strict subset, so a full run keeps the strict body
  const topicIds = book === null && emptyOnly && empty.length < all.length ? empty.map((topic) => topic.id) : null

  return (
    <section className="section" id="questions" aria-labelledby="bank-title">
      <div className="section-head">
        <h2 id="bank-title">{b.title}</h2>
        <Link className="btn small" to={`/library/${subjectId}/questions`}>
          {b.review(questionCount)}
        </Link>
      </div>
      {reviewCount > 0 && (
        <Notice kind="warn">
          <p>
            <Link to={`/library/${subjectId}/questions?filter=review`}>{libraryStrings.versions.reviewLink(reviewCount)}</Link>
            {libraryStrings.versions.reviewAfter}
          </p>
        </Notice>
      )}
      <h3 className="mt-6">{b.generateMore}</h3>
      <div className="fields">
        <div className="field">
          <label className="label" htmlFor="bank-per-topic">
            {b.perTopic}
          </label>
          <input
            className="input num"
            id="bank-per-topic"
            type="number"
            min={1}
            max={10}
            value={perTopic}
            onChange={(event) => setPerTopic(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="bank-style">
            {b.style}
          </label>
          <select
            className="select"
            id="bank-style"
            value={style}
            onChange={(event) => setStyle(event.target.value)}
          >
            <option value="mixed">{b.mixed}</option>
            <option value="short">{b.styles.short}</option>
            <option value="open">{b.styles.open}</option>
          </select>
        </div>
        <div className="field">
          <label className="label" htmlFor="bank-difficulty">
            {b.difficulty}
          </label>
          <select
            className="select"
            id="bank-difficulty"
            value={difficulty}
            onChange={(event) => setDifficulty(event.target.value)}
          >
            <option value="mixed">{b.mixed}</option>
            <option value="recall">{b.difficulties.recall}</option>
            <option value="apply">{b.difficulties.apply}</option>
            <option value="analysis">{b.difficulties.analysis}</option>
          </select>
        </div>
        <div className="field">
          <label className="label" htmlFor="bank-sources">
            {src.label}
          </label>
          <select
            className="select"
            id="bank-sources"
            value={sourcePolicy}
            aria-describedby="bank-sources-hint"
            onChange={(event) =>
              setSourcePolicy(POLICIES.find((policy) => policy === event.target.value) ?? 'materials')
            }
          >
            <option value="materials">{src.materials}</option>
            <option value="expanded">{c.sourcesExpanded}</option>
            <option value="outside">{c.sourcesOutside}</option>
          </select>
          <p className="hint" id="bank-sources-hint">
            {c.sourcesHint}
          </p>
        </div>
        <div className="field">
          <label className="label" htmlFor="bank-for">
            {c.generateFor}
          </label>
          <select className="select" id="bank-for" value={chosen} onChange={(event) => setChoice(event.target.value)}>
            {books.map((doc) => (
              <option key={doc.id} value={`book:${doc.latest_version.id}`}>
                {c.fromBook(doc.title, doc.latest_version.topic_count ?? 0)}
              </option>
            ))}
            <option value="all">{b.allTopics(all.length)}</option>
          </select>
        </div>
      </div>
      {book === null && (
        <label className="check mt-2">
          <input
            type="checkbox"
            checked={emptyOnly}
            disabled={empty.length === 0}
            onChange={(event) => setOnlyEmpty(event.target.checked)}
          />{' '}
          {b.onlyEmpty(empty.length)}
        </label>
      )}
      {!hasTopics && <p className="hint">{b.needTopics}</p>}
      <EstimateLine
        generation={
          hasTopics && valid
            ? {
                subject_id: subjectId,
                per_topic: count,
                style,
                difficulty,
                ...(sourcePolicy !== 'materials' && { source_policy: sourcePolicy }),
                ...(topicIds && { topic_ids: topicIds }),
                ...(book !== null && { document_version_id: book }),
                ...(chapterId !== null && { chapter_id: chapterId }),
              }
            : null
        }
      />
      <div className="actions mt-4">
        <button
          className="btn"
          type="button"
          disabled={!canRun || !hasTopics || busy || !valid}
          onClick={() =>
            // the default stays implicit, so the strict body is unchanged
            onGenerate({
              per_topic: count,
              style,
              difficulty,
              ...(sourcePolicy !== 'materials' && { source_policy: sourcePolicy }),
              ...(topicIds && { topic_ids: topicIds }),
              ...(book !== null && { document_version_id: book }),
              ...(chapterId !== null && { chapter_id: chapterId }),
            })
          }
        >
          {b.generate}
        </button>
      </div>
    </section>
  )
}
