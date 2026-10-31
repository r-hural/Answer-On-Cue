import { useCallback, useEffect, useState } from 'react'
import { type MonthSummary, fetchSummary } from '../costs/api'
import { monthName, percentUsed, usd } from '../costs/format'
import { type Note, NoteNotice } from '../../ui/Notice'
import { type Preferences, savePreferences } from './api'
import { settingsStrings } from './strings'

const s = settingsStrings.spending

export function SpendingSection({
  preferences,
  onSaved,
}: {
  preferences: Preferences
  onSaved: (preferences: Preferences) => void
}) {
  const ceiling = preferences.spending_ceiling_usd ?? 0
  const [value, setValue] = useState(ceiling > 0 ? String(ceiling) : '')
  const [summary, setSummary] = useState<MonthSummary | null>(null)
  const [message, setMessage] = useState<Note | null>(null)
  // A number field reports text it cannot parse as '' (badInput): that is not "no ceiling".
  const [badInput, setBadInput] = useState(false)

  const reload = useCallback(async () => {
    try {
      setSummary(await fetchSummary())
    } catch {
      setSummary(null)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchSummary()
      .then((loaded) => !cancelled && setSummary(loaded))
      .catch(() => !cancelled && setSummary(null))
    return () => {
      cancelled = true
    }
  }, [])

  async function save() {
    setMessage(null)
    const ceilingUsd = value.trim() ? Number(value) : 0
    if (badInput || !Number.isFinite(ceilingUsd) || ceilingUsd < 0) {
      setMessage({ text: s.invalid, error: true })
      return
    }
    try {
      const saved = await savePreferences({ spending_ceiling_usd: ceilingUsd })
      onSaved(saved)
      setMessage({ text: s.saved, error: false })
      void reload()
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  const month = summary?.month
  const percent = month ? percentUsed(month.spent_usd, month.ceiling_usd) : 0

  return (
    <section className="set" id="spending" aria-labelledby="spending-title">
      <h2 id="spending-title">{s.title}</h2>
      <div className="cols-2 mt-4">
        <div>
          <div className="field">
            <label className="label" htmlFor="ceil">
              {s.ceiling}
            </label>
            <input
              className="input short"
              id="ceil"
              type="number"
              min={0}
              max={10000}
              step="any"
              placeholder={s.notSet}
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
                setBadInput(event.target.validity?.badInput ?? false)
              }}
            />
            <span className="hint">{s.hint}</span>
          </div>
          <div className="actions mt-4">
            <button type="button" className="btn" onClick={() => void save()}>
              {s.save}
            </button>
          </div>
          <NoteNotice note={message} />
        </div>
        {month && (
          <div className="scores self-start">
            <div>
              <p className="k">{s.soFar(monthName(month.month).split(' ')[0])}</p>
              {month.ceiling_usd > 0 ? (
                <>
                  <p className="v measure">
                    {usd(month.spent_usd)} <small>{s.of(usd(month.ceiling_usd))}</small>
                  </p>
                  <div className="meter-bar" role="img" aria-label={s.meter(percent)}>
                    <span style={{ width: `${percent}%` }} />
                  </div>
                </>
              ) : (
                <>
                  <p className="v measure">{usd(month.spent_usd)}</p>
                  <p className="of">{s.noCeiling}</p>
                </>
              )}
              <p className="small mt-3">
                {/* a plain link: the settings page renders outside any router in its tests */}
                <a href="/settings/usage">{s.usageLink}</a>
              </p>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
