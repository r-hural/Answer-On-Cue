import { useId, useState } from 'react'
import { type Question, type Rubric, editQuestion } from './api'
import { bankStrings } from './strings'
import { useContentLanguage } from '../language/ContentLanguage'

const s = bankStrings.editForm

export function EditForm({
  question,
  onSaved,
  onCancel,
}: {
  question: Question
  onSaved: (question: Question) => void
  onCancel: () => void
}) {
  const cl = useContentLanguage()
  const id = useId()
  const [text, setText] = useState(question.text)
  const [style, setStyle] = useState<string>(question.style)
  const [difficulty, setDifficulty] = useState<string>(question.difficulty)
  const [modelAnswer, setModelAnswer] = useState(question.rubric.model_answer)
  const [points, setPoints] = useState(
    question.rubric.essential_points.map((p) => ({ text: p.text, weight: String(p.weight) })),
  )
  const [error, setError] = useState<string | null>(null)

  const update = (index: number, patch: Partial<{ text: string; weight: string }>) =>
    setPoints((current) => current.map((p, i) => (i === index ? { ...p, ...patch } : p)))

  async function save() {
    const rubric: Rubric = {
      ...question.rubric,
      model_answer: modelAnswer,
      essential_points: question.rubric.essential_points.map((point, i) => ({
        ...point,
        text: points[i].text,
        weight: Number(points[i].weight),
      })),
    }
    try {
      setError(null)
      onSaved(await editQuestion(question.version_id, { text, style, difficulty, rubric }))
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  return (
    <form
      className="setup"
      aria-label={s.label}
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <fieldset>
        <legend>
          <h2>{s.question}</h2>
        </legend>
        <p className="hint mb-4">{s.newVersion(question.version + 1)}</p>
        <div className="field">
          <label className="label" htmlFor={`${id}-text`}>
            {s.text}
          </label>
          <textarea
            className="textarea content full"
            id={`${id}-text`}
            lang={cl.bcp47}
            rows={3}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <div className="fields mt-5">
          <div className="field">
            <label className="label" htmlFor={`${id}-style`}>
              {s.style}
            </label>
            <select
              className="select"
              id={`${id}-style`}
              value={style}
              onChange={(event) => setStyle(event.target.value)}
            >
              <option value="short">{bankStrings.styles.short}</option>
              <option value="open">{bankStrings.styles.open}</option>
            </select>
          </div>
          <div className="field">
            <label className="label" htmlFor={`${id}-difficulty`}>
              {s.difficulty}
            </label>
            <select
              className="select"
              id={`${id}-difficulty`}
              value={difficulty}
              onChange={(event) => setDifficulty(event.target.value)}
            >
              <option value="recall">{bankStrings.difficulties.recall}</option>
              <option value="apply">{bankStrings.difficulties.apply}</option>
              <option value="analysis">{bankStrings.difficulties.analysis}</option>
            </select>
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>
          <h2>{s.points}</h2>
        </legend>
        <div className="rubric">
          <div className="rubric-row rubric-head" aria-hidden="true">
            <span>{s.pointHead}</span>
            <span>{s.weightHead}</span>
          </div>
          {points.map((point, index) => (
            <div key={index} className="rubric-row">
              <textarea
                className="textarea content grow"
                lang={cl.bcp47}
                rows={1}
                aria-label={s.pointText(index + 1)}
                value={point.text}
                onChange={(event) => update(index, { text: event.target.value })}
              />
              <input
                className="input num"
                type="number"
                min={0.01}
                step="any"
                aria-label={s.pointWeight(index + 1)}
                value={point.weight}
                onChange={(event) => update(index, { weight: event.target.value })}
              />
            </div>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>
          <h2>{s.modelAnswer}</h2>
        </legend>
        <div className="field">
          <label className="visually-hidden" htmlFor={`${id}-model`}>
            {s.modelAnswer}
          </label>
          <textarea
            className="textarea content full"
            id={`${id}-model`}
            lang={cl.bcp47}
            rows={5}
            value={modelAnswer}
            onChange={(event) => setModelAnswer(event.target.value)}
          />
        </div>
      </fieldset>

      <fieldset>
        <div className="actions">
          <button type="submit" className="btn primary">
            {s.save}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {s.cancel}
          </button>
        </div>
        {error && (
          <div className="notice error mt-4" role="alert">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="10" cy="10" r="7.5" />
              <path d="M10 6v4.5" />
              <path d="M10 13.5v.01" />
            </svg>
            <div>{error}</div>
          </div>
        )}
      </fieldset>
    </form>
  )
}
