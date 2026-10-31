import { useState } from 'react'
import { type Question, type Review, type SourceChange, type Verdict, checkQuestion, keepQuestion, putReview } from './api'
import { EditForm } from './EditForm'
import { bankStrings } from './strings'
import { Notice } from '../../ui/Notice'
import { basisLabel, questionPolicyLabel } from '../rounds/sourcePolicy'
import { type DiffToken, side, wordDiff } from './wordDiff'
import { useContentLanguage } from '../language/ContentLanguage'

const s = bankStrings
const VERDICTS: Verdict[] = ['ok', 'weak', 'bad']
const RATED_CHIP: Record<Verdict, string> = { ok: 'chip ok', weak: 'chip warn', bad: 'chip error' }
const v = bankStrings.versions
const c = bankStrings.checked

/** A passage with its changed words in <mark>. */
function MarkedText({ tokens }: { tokens: DiffToken[] }) {
  const cl = useContentLanguage()
  return (
    <p lang={cl.bcp47}>
      {tokens.map((token, index) =>
        token.kind === 'same' ? (
          <span key={index}>{cl.typeset(token.text)}</span>
        ) : (
          <mark key={index}>{cl.typeset(token.text)}</mark>
        ),
      )}
    </p>
  )
}

/** The cited passage in the version the question uses and in the newer one (bank--needs-review). */
function SourceComparison({ change }: { change: SourceChange }) {
  const cl = useContentLanguage()
  const tokens = change.new ? wordDiff(change.old.text, change.new.text) : []
  return (
    <div className="cols-2">
      <div>
        <h4 className="sub-h">{v.usedNow(change.old_version)}</h4>
        <blockquote className="source-quote">
          <span className="source" lang={cl.bcp47}>
            {change.old.locator}
          </span>
          {change.new ? <MarkedText tokens={side(tokens, 'old')} /> : <p lang={cl.bcp47}>{cl.typeset(change.old.text)}</p>}
        </blockquote>
      </div>
      <div>
        <h4 className="sub-h">{change.new ? v.inVersion(change.new_version) : v.removedIn(change.new_version)}</h4>
        {change.new && (
          <blockquote className="source-quote">
            <span className="source" lang={cl.bcp47}>
              {change.new.locator}
            </span>
            <MarkedText tokens={side(tokens, 'new')} />
          </blockquote>
        )}
      </div>
    </div>
  )
}

function AlertIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 6v4.5" />
      <path d="M10 13.5v.01" />
    </svg>
  )
}

export function QuestionCard({
  question,
  review,
  onReview,
  onEdited,
  onRetire,
}: {
  question: Question
  review: Review | undefined
  onReview: (review: Review) => void
  onEdited: (question: Question) => void
  onRetire: (question: Question) => Promise<void>
}) {
  const cl = useContentLanguage()
  const changes = question.source_changes ?? []
  const [note, setNote] = useState(review?.note ?? '')
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const locators = new Map(question.passages.map((p) => [p.id, p.locator]))

  async function save(verdict: Verdict, nextNote: string) {
    try {
      setError(null)
      onReview(await putReview(question.version_id, verdict, nextNote.trim() || null))
    } catch (failure) {
      setError((failure as Error).message || s.saveError)
    }
  }

  async function keep() {
    try {
      setError(null)
      onEdited(await keepQuestion(question.version_id))
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  async function markChecked() {
    setChecking(true)
    try {
      setError(null)
      onEdited(await checkQuestion(question.version_id))
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setChecking(false)
    }
  }

  async function retire() {
    if (!window.confirm(s.confirmRetire)) return
    try {
      setError(null)
      await onRetire(question)
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  const needsReview = question.status === 'needs_review'
  const canCheck = needsReview && changes.length === 0
  const points = question.rubric.essential_points
  const beyond = points.filter((point) => point.basis === 'expanded' || point.basis === 'mixed').length
  const passages = new Map(question.passages.map((p) => [p.id, p]))
  return (
    <article aria-label={question.text} className="stack">
      <div className="q-head">
        <div className="chips">
          <span className="chip">{s.styles[question.style]}</span>
          <span className="chip">{s.difficulties[question.difficulty]}</span>
          <span className="chip">{basisLabel(question.basis)}</span>
          {(question.source_policy === 'expanded' || question.source_policy === 'outside') && (
            <span className="chip">{questionPolicyLabel(question.source_policy)}</span>
          )}
          <span className={needsReview ? 'chip warn' : 'chip'}>{s.status[question.status] ?? question.status}</span>
          {review && <span className={RATED_CHIP[review.verdict]}>{s.rated[review.verdict]}</span>}
        </div>
        <span className="small muted">
          {s.version(question.version)}
          {v.origin[question.origin ?? 'ai'] ?? ''}
        </span>
      </div>
      <h3 className="q-text" lang={cl.bcp47}>
        {cl.typeset(question.text)}
      </h3>
      {needsReview && question.review_note && (
        <div className="notice warn">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M10 3 2.5 16.5h15z" />
            <path d="M10 8.5v3.5" />
            <path d="M10 14.5v.01" />
          </svg>
          <div>{question.review_note}</div>
        </div>
      )}
      {changes.length > 0 && (
        <>
          {changes.map((change) => (
            <SourceComparison key={change.old.id} change={change} />
          ))}
          <p className="small muted">{v.marked(changes[0].old_version)}</p>
        </>
      )}
      {(question.rubric.source_issues ?? []).map((issue, index) => (
        <Notice kind="warn" key={index}>
          <div className="flow">
            <p>
              <strong>{s.issues[issue.kind]}</strong>
            </p>
            <p className="content" lang={cl.bcp47}>
              {cl.typeset(issue.note)}
            </p>
            {issue.passage_ids.map((id) => {
              const passage = passages.get(id)
              if (!passage) return null
              return (
                <blockquote key={id} className="source-quote">
                  <span className="source" lang={cl.bcp47}>
                    {passage.locator}
                  </span>
                  <p lang={cl.bcp47}>{cl.typeset(passage.text)}</p>
                </blockquote>
              )
            })}
          </div>
        </Notice>
      ))}
      {editing ? (
        <EditForm
          question={question}
          onCancel={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false)
            onEdited(saved)
          }}
        />
      ) : (
        <>
          <div className="cols-2">
            <div>
              <h4 className="sub-h">{s.points}</h4>
              <ol className="keypoints" lang={cl.bcp47}>
                {question.rubric.essential_points.map((point) => (
                  <li key={point.id}>
                    {cl.typeset(point.text)}{' '}
                    <span className="w">
                      <span lang="en">{s.weight(point.weight)}</span>
                      {point.passage_ids.length > 0 && (
                        <>
                          {' · '}
                          {point.passage_ids.map((id) => locators.get(id) ?? `#${id}`).join(', ')}
                        </>
                      )}
                    </span>
                    {(point.basis === 'expanded' || point.basis === 'mixed') && (
                      <span className="note" lang="en">
                        {' '}
                        {point.basis === 'expanded' ? s.beyond : s.partlyBeyond}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
              {beyond > 0 && <p className="small muted">{s.expandedLine(beyond, points.length)}</p>}
            </div>
            <div>
              <h4 className="sub-h">{s.modelAnswer}</h4>
              <p className="model" lang={cl.bcp47}>
                {cl.typeset(question.rubric.model_answer)}
              </p>
              <details className="disclosure small">
                <summary>{s.passages(question.passages.length)}</summary>
                {question.passages.map((passage) => (
                  <blockquote key={passage.id} className="source-quote">
                    <span className="source" lang={cl.bcp47}>
                      {passage.locator}
                    </span>
                    <p lang={cl.bcp47}>{cl.typeset(passage.text)}</p>
                  </blockquote>
                ))}
              </details>
            </div>
          </div>
          <div className="q-foot">
            <fieldset className="rate">
              <legend>{s.verdictLabel}</legend>
              {VERDICTS.map((verdict) => (
                <label key={verdict} className="choice">
                  <input
                    type="radio"
                    name={`verdict-${question.version_id}`}
                    value={verdict}
                    checked={review?.verdict === verdict}
                    onChange={() => save(verdict, note)}
                  />
                  {s.verdicts[verdict]}
                </label>
              ))}
              <input
                className="input"
                type="text"
                aria-label={s.note}
                placeholder={s.notePlaceholder}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                onBlur={() => {
                  if (review && (review.note ?? '') !== note.trim()) void save(review.verdict, note)
                }}
              />
            </fieldset>
            {changes.length > 0 && <p className="small muted">{v.keepHint}</p>}
            {canCheck && <p className="small muted">{c.hint}</p>}
            <div className="actions">
              {canCheck && (
                <button type="button" className="btn small" disabled={checking} onClick={() => void markChecked()}>
                  {checking ? c.working : c.button}
                </button>
              )}
              {changes.length > 0 && (
                <button type="button" className="btn small" onClick={() => void keep()}>
                  {v.keep}
                </button>
              )}
              <button type="button" className="btn small" onClick={() => setEditing(true)}>
                {s.edit}
              </button>
              <button type="button" className="btn small danger" onClick={retire}>
                {s.retire}
              </button>
            </div>
          </div>
        </>
      )}
      {error && (
        <div className="notice error" role="alert">
          <AlertIcon />
          <div>{error}</div>
        </div>
      )}
    </article>
  )
}
