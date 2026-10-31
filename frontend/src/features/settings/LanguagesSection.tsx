import { useEffect, useState } from 'react'
import { type ContentLanguage, listLanguages } from '../library/api'
import { type Note, NoteNotice } from '../../ui/Notice'
import { type Preferences, savePreferences, updateLanguage } from './api'
import { LanguageVoice } from './LanguageVoice'
import { settingsStrings } from './strings'

const l = settingsStrings.languages

export function LanguagesSection({
  preferences,
  keysVersion,
  onPreferences,
}: {
  preferences: Preferences
  keysVersion: number
  onPreferences: (preferences: Preferences) => void
}) {
  const [rows, setRows] = useState<ContentLanguage[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [rate, setRate] = useState(preferences.voice_rate)
  const [rateNote, setRateNote] = useState<Note | null>(null)

  useEffect(() => {
    let cancelled = false
    listLanguages()
      .then((list) => !cancelled && setRows(list))
      .catch((error) => !cancelled && setLoadError((error as Error).message))
    return () => {
      cancelled = true
    }
  }, [])

  function replace(row: ContentLanguage) {
    setRows((current) => current.map((candidate) => (candidate.code === row.code ? row : candidate)))
  }

  async function toggle(row: ContentLanguage, enabled: boolean) {
    try {
      replace(await updateLanguage(row.code, { enabled }))
      setErrors((current) => ({ ...current, [row.code]: '' }))
    } catch (error) {
      setErrors((current) => ({ ...current, [row.code]: (error as Error).message }))
    }
  }

  async function saveRate() {
    try {
      onPreferences(await savePreferences({ voice_rate: rate }))
      setRateNote({ text: l.rateSaved, error: false })
    } catch (error) {
      setRateNote({ text: (error as Error).message, error: true })
    }
  }

  return (
    <section className="set" id="languages" aria-labelledby="languages-title">
      <h2 id="languages-title">{l.title}</h2>
      <p className="hint">{l.intro}</p>
      <div className="fields mt-5">
        <div className="field">
          <label className="label" htmlFor="v-rate">
            {settingsStrings.voice.rate}
          </label>
          <div className="actions">
            <input
              id="v-rate"
              type="range"
              min={0.5}
              max={1.5}
              step={0.1}
              value={rate}
              onChange={(event) => setRate(Number(event.target.value))}
            />
            <output className="num" htmlFor="v-rate">
              {rate.toFixed(1)}
            </output>
            <button type="button" className="btn" onClick={saveRate}>
              {l.rateSave}
            </button>
          </div>
        </div>
      </div>
      <NoteNotice note={rateNote} />
      {loadError && (
        <p className="small text-error mt-2" role="alert">
          {loadError}
        </p>
      )}
      <ul className="stack mt-5" aria-label="Content languages">
        {rows.map((row) => {
          const locked = row.enabled && row.subject_count > 0
          return (
            <li key={row.code} className="mt-4">
              <div className="actions">
                <strong>{row.name_en}</strong>
                {row.name_native !== row.name_en && <span className="muted">{row.name_native}</span>}
                {row.subject_count > 0 && <span className="small muted">{l.used(row.subject_count)}</span>}
                <label className="choice">
                  <input
                    type="checkbox"
                    aria-label={l.enable(row.name_en)}
                    checked={row.enabled}
                    disabled={locked}
                    onChange={(event) => toggle(row, event.target.checked)}
                  />
                  {l.enabled}
                </label>
              </div>
              {locked && <p className="hint">{l.inUse}</p>}
              {errors[row.code] && (
                <p className="small text-error mt-2" role="alert">
                  {errors[row.code]}
                </p>
              )}
              {row.enabled && (
                <LanguageVoice
                  language={row}
                  preferences={preferences}
                  keysVersion={keysVersion}
                  onSaved={replace}
                  rate={rate}
                />
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
