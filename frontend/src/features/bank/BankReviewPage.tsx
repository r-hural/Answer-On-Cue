import { useEffect, useLayoutEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  type Basis,
  type Question,
  type Review,
  type TopicRef,
  exportUrl,
  getQuestion,
  getSubject,
  listQuestionIds,
  listReviews,
  listTopics,
  retireQuestion,
} from './api'
import { QuestionCard } from './QuestionCard'
import { bankStrings } from './strings'
import { basisLabel } from '../rounds/sourcePolicy'
import { Notice } from '../../ui/Notice'
import { ContentLanguageHost, useContentLanguage } from '../language/ContentLanguage'

type Filter = 'all' | 'unrated' | 'problems' | 'review'
const FILTERS: Filter[] = ['all', 'unrated', 'problems', 'review']
const BASES: Basis[] = ['source', 'expanded', 'mixed']

interface Loaded {
  name: string
  language: string | null
  topics: TopicRef[]
  questions: Question[]
  reviews: Map<number, Review>
}

async function load(subjectId: number): Promise<Loaded> {
  const [subject, topics, ids, reviews] = await Promise.all([
    getSubject(subjectId),
    listTopics(subjectId),
    listQuestionIds(subjectId),
    listReviews(subjectId),
  ])
  const questions = await Promise.all(ids.map(getQuestion))
  return {
    name: subject.name,
    language: subject.language ?? null,
    topics,
    questions,
    reviews: new Map(reviews.map((review) => [review.question_version_id, review])),
  }
}

export function BankReviewPage() {
  return <ContentLanguageHost>{(setCode) => <BankReview onLanguage={setCode} />}</ContentLanguageHost>
}

function BankReview({ onLanguage }: { onLanguage: (code: string | null) => void }) {
  const cl = useContentLanguage()
  const subjectId = Number(useParams().subjectId)
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [params] = useSearchParams()
  // ?filter=review (from a replaced document) opens the same tab as ?show=review
  const requested = (params.get('show') ?? params.get('filter')) as Filter | null
  const filter: Filter = requested && FILTERS.includes(requested) ? requested : 'all'
  const [source, setSource] = useState<Basis | 'all'>('all')
  const language = data?.language ?? null
  useLayoutEffect(() => onLanguage(language), [onLanguage, language])

  useEffect(() => {
    let cancelled = false
    load(subjectId)
      .then((loaded) => !cancelled && setData(loaded))
      .catch(() => !cancelled && setError(bankStrings.loadError))
    return () => {
      cancelled = true
    }
  }, [subjectId])

  if (error)
    return (
      <div className="notice error" role="alert">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="10" cy="10" r="7.5" />
          <path d="M10 6v4.5" />
          <path d="M10 13.5v.01" />
        </svg>
        <div>{error}</div>
      </div>
    )
  if (!data) return <p className="muted">{bankStrings.loading}</p>

  const patch = (update: (current: Loaded) => Loaded) =>
    setData((current) => (current ? update(current) : current))

  const onReview = (review: Review) =>
    patch((current) => ({
      ...current,
      reviews: new Map(current.reviews).set(review.question_version_id, review),
    }))

  // An edit makes a new version: it replaces the old one in the list and starts without a verdict.
  const onEdited = (old: Question) => (saved: Question) =>
    patch((current) => ({
      ...current,
      questions: current.questions.map((q) => (q.version_id === old.version_id ? saved : q)),
    }))

  const onRetire = async (question: Question) => {
    await retireQuestion(question.version_id)
    patch((current) => ({
      ...current,
      questions: current.questions.filter((q) => q.version_id !== question.version_id),
    }))
  }

  const verdictOf = (question: Question) => data.reviews.get(question.version_id)?.verdict
  const matches = (question: Question, which: Filter) => {
    if (which === 'unrated') return verdictOf(question) === undefined
    if (which === 'problems') return verdictOf(question) === 'weak' || verdictOf(question) === 'bad'
    if (which === 'review') return question.status === 'needs_review'
    return true
  }
  const count = (which: Filter) => data.questions.filter((q) => matches(q, which)).length
  const shown = data.questions.filter(
    (question) => matches(question, filter) && (source === 'all' || (question.basis ?? 'source') === source),
  )
  const reviewed = data.questions.filter((question) => verdictOf(question) !== undefined).length
  const flagged = count('review')
  const changed = data.questions.filter((q) => (q.source_changes ?? []).length > 0).length
  const known = new Set(data.topics.map((topic) => topic.id))
  const sections = [
    ...data.topics.map((topic) => ({
      key: String(topic.id),
      title: topic.title,
      content: true,
      questions: shown.filter((q) => q.primary_topic_id === topic.id),
    })),
    {
      key: 'other',
      title: bankStrings.other,
      content: false,
      questions: shown.filter((q) => q.primary_topic_id === null || !known.has(q.primary_topic_id)),
    },
  ].filter((section) => section.questions.length > 0)

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <Link to="/library">{bankStrings.library}</Link> ›{' '}
            <Link to={`/library/${subjectId}`}>
              {data.name}
            </Link>{' '}
            › {bankStrings.crumb}
          </p>
          <h1>{bankStrings.title}</h1>
          {data.questions.length > 0 && (
            <p className="num">
              {bankStrings.total(data.questions.length)} · <span>{bankStrings.counter(reviewed, data.questions.length)}</span>
              {flagged > 0 && <> · {bankStrings.needReview(flagged)}</>}
            </p>
          )}
        </div>
        {data.questions.length > 0 && (
          <div className="actions">
            <a className="btn" href={exportUrl(subjectId)} download>
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M10 3v10" />
                <path d="M6 9l4 4 4-4" />
                <path d="M4 16h12" />
              </svg>
              {bankStrings.export}
            </a>
          </div>
        )}
      </div>

      {data.questions.length === 0 ? (
        <div className="empty">
          <p>{bankStrings.empty}</p>
        </div>
      ) : (
        <nav className="tabs" aria-label={bankStrings.filter}>
          {FILTERS.map((which) => (
            <Link
              key={which}
              to={{ search: which === 'all' ? '' : `?show=${which}` }}
              aria-current={filter === which ? 'page' : undefined}
            >
              {bankStrings.tab(bankStrings.filters[which], count(which))}
            </Link>
          ))}
        </nav>
      )}
      {changed > 0 && (
        <Notice kind="warn" className="mb-6">
          <strong>{bankStrings.versions.noticeTitle(changed)}</strong> {bankStrings.versions.noticeBody}
        </Notice>
      )}
      {data.questions.length > 0 && (
        <div className="filters">
          <div className="field">
            <label className="label" htmlFor="pol">
              {bankStrings.source}
            </label>
            <select
              className="select"
              id="pol"
              value={source}
              onChange={(event) => setSource(event.target.value as Basis | 'all')}
            >
              <option value="all">{bankStrings.sources.all}</option>
              {BASES.map((basis) => (
                <option key={basis} value={basis}>
                  {basisLabel(basis)}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {sections.map((section, index) => (
        <section key={section.key} aria-label={section.title} className={index > 0 ? 'section' : undefined}>
          <div className="section-head">
            {section.content ? (
              <h2 className="content-title" lang={cl.bcp47}>
                {cl.typeset(section.title)}
              </h2>
            ) : (
              <h2>{section.title}</h2>
            )}
          </div>
          <ol className="rows zebra">
            {section.questions.map((question) => (
              <li key={question.version_id} className="q-item">
                <QuestionCard
                  question={question}
                  review={data.reviews.get(question.version_id)}
                  onReview={onReview}
                  onEdited={onEdited(question)}
                  onRetire={onRetire}
                />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </>
  )
}
