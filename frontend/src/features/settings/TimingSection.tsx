import { useState } from 'react'
import { type Preferences, savePreferences } from './api'
import { type Note, NoteNotice } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.timing

export function TimingSection({
  preferences,
  onSaved,
}: {
  preferences: Preferences
  onSaved: (preferences: Preferences) => void
}) {
  const [count, setCount] = useState(String(preferences.round_question_count))
  const [prep, setPrep] = useState(String(preferences.round_prep_seconds))
  const [answer, setAnswer] = useState(String(preferences.round_answer_seconds))
  const [message, setMessage] = useState<Note | null>(null)

  async function save() {
    try {
      onSaved(
        await savePreferences({
          round_question_count: Number(count),
          round_prep_seconds: Number(prep),
          round_answer_seconds: Number(answer),
        }),
      )
      setMessage({ text: s.saved, error: false })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  return (
    <section className="set" id="targets" aria-labelledby="targets-title">
      <h2 id="targets-title">{s.title}</h2>
      <h3 className="mt-4">{s.defaults}</h3>
      <div className="fields">
        <div className="field">
          <label className="label" htmlFor="t-n">
            {s.questions}
          </label>
          <input
            className="input"
            id="t-n"
            type="number"
            min={1}
            max={30}
            value={count}
            onChange={(event) => setCount(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="t-p">
            {s.prep}
          </label>
          <input
            className="input"
            id="t-p"
            type="number"
            min={0}
            max={600}
            value={prep}
            onChange={(event) => setPrep(event.target.value)}
          />
          <span className="hint">{s.prepHint}</span>
        </div>
        <div className="field">
          <label className="label" htmlFor="t-s">
            {s.answer}
          </label>
          <input
            className="input"
            id="t-s"
            type="number"
            min={5}
            max={600}
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
          />
        </div>
      </div>
      <div className="actions">
        <button type="button" className="btn" onClick={save}>
          {s.save}
        </button>
      </div>
      <NoteNotice note={message} />
    </section>
  )
}
