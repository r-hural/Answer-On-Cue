import { useState } from 'react'
import { type Preferences, savePreferences } from './api'
import { type Note, NoteNotice } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.scoring

export function ScoringSection({
  preferences,
  onSaved,
}: {
  preferences: Preferences
  onSaved: (preferences: Preferences) => void
}) {
  const [correctness, setCorrectness] = useState(String(preferences.factual_weights[0]))
  const [bounds, setBounds] = useState(preferences.factual_band_bounds.map(String))
  const [message, setMessage] = useState<Note | null>(null)

  const weight = Number(correctness)
  const completeness = correctness.trim() === '' || !Number.isFinite(weight) ? '' : 80 - weight

  async function save() {
    try {
      onSaved(
        await savePreferences({
          factual_weights: [weight, 80 - weight],
          factual_band_bounds: bounds.map(Number) as [number, number, number],
        }),
      )
      setMessage({ text: s.saved, error: false })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  return (
    <section className="set" id="scoring" aria-labelledby="scoring-title">
      <h2 id="scoring-title">{s.title}</h2>
      <p className="lead">{s.note}</p>
      <h3>{s.weights}</h3>
      <div className="fields narrow">
        <div className="field">
          <label className="label" htmlFor="a-correctness">
            {s.correctness}
          </label>
          <input
            className="input"
            id="a-correctness"
            type="number"
            min={10}
            max={70}
            value={correctness}
            onChange={(event) => setCorrectness(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="a-completeness">
            {s.completeness}
          </label>
          <input
            className="input"
            id="a-completeness"
            type="number"
            readOnly
            value={completeness}
          />
        </div>
        <div className="field">
          <span className="label">{s.language}</span>
          <span className="fixed-value">
            20 <span className="muted small">&nbsp;{s.languageFixed}</span>
          </span>
        </div>
      </div>
      <p className="hint mt-3">{s.weightsHint}</p>
      <h3>{s.bands}</h3>
      <div className="table-wrap">
        <table className="table middle">
          <thead>
            <tr>
              <th scope="col">{s.band}</th>
              <th scope="col">{s.from}</th>
              <th scope="col">{s.modelAnswer}</th>
              <th scope="col">{s.retry}</th>
              <th scope="col">{s.coverage}</th>
            </tr>
          </thead>
          <tbody>
            {s.rows.map((row, index) => (
              <tr key={row.cls}>
                <td>
                  <span className={`band ${row.cls}`}>{row.label}</span>
                </td>
                <td className={index === 0 ? 'num' : undefined}>
                  {index === 0 ? (
                    '0'
                  ) : (
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={99}
                      aria-label={s.fromLabel(row.label)}
                      value={bounds[index - 1]}
                      onChange={(event) =>
                        setBounds((current) =>
                          current.map((old, i) => (i === index - 1 ? event.target.value : old)),
                        )
                      }
                    />
                  )}
                </td>
                <td>{row.model}</td>
                <td>{row.retry}</td>
                <td>{row.coverage}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
