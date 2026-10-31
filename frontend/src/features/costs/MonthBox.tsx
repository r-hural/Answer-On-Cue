import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type MonthSummary, fetchSummary } from './api'
import { percentUsed, usd } from './format'
import { costsStrings as s } from './strings'

/** Home's "This month" box (home.html): rounds, AI usage and the ceiling's meter. */
export function MonthBox() {
  const [summary, setSummary] = useState<MonthSummary | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchSummary()
      .then((loaded) => !cancelled && setSummary(loaded))
      .catch(() => !cancelled && setSummary(null))
    return () => {
      cancelled = true
    }
  }, [])

  if (!summary) return null
  const month = summary.month
  const spent = usd(month.spent_usd)
  const ceiling = month.ceiling_usd > 0 ? ` ${s.monthOfCeiling(usd(month.ceiling_usd))}` : ''
  const percent = percentUsed(month.spent_usd, month.ceiling_usd)
  return (
    <section className="box tint" aria-labelledby="month-h">
      <h3 id="month-h">{s.monthTitle}</h3>
      <p className="num mt-3">
        {s.monthRounds(summary.rounds)} · {s.monthUsage(spent)}
        {ceiling}
      </p>
      {month.ceiling_usd > 0 && (
        <div className="meter-bar mt-3" role="img" aria-label={s.meter(percent)}>
          <span style={{ width: `${percent}%` }} />
        </div>
      )}
      <p className="mt-3">
        <Link to="/settings/usage">{s.usageLink}</Link>
      </p>
    </section>
  )
}
