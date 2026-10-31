import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  type MonthSummary,
  type PriceRow,
  type UsageGroup,
  type UsageRow,
  type UsageTable,
  fetchPrices,
  fetchSummary,
  fetchUsage,
} from '../costs/api'
import { monthName, percentUsed, thousands, usd } from '../costs/format'
import { operationLabel, providerLabel } from '../costs/strings'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.spending.usage

const GROUPS: UsageGroup[] = ['operation', 'round', 'subject', 'month']

const isGroup = (value: string | null): value is UsageGroup =>
  GROUPS.includes(value as UsageGroup)

function amount(row: UsageRow): string {
  const parts: string[] = []
  if (row.audio_minutes > 0) parts.push(s.audio(Math.round(row.audio_minutes)))
  if (row.input_tokens || row.output_tokens)
    parts.push(s.tokens(thousands(row.input_tokens), thousands(row.output_tokens)))
  if (row.characters > 0) parts.push(s.characters(thousands(row.characters)))
  return parts.join(' · ') || '—'
}

function Money({ value }: { value: number | null }) {
  if (value === null)
    return (
      <td className="r" aria-label={s.notReported}>
        —
      </td>
    )
  return <td className="r">{usd(value)}</td>
}

function RowName({ row, group }: { row: UsageRow; group: UsageGroup }) {
  if (group === 'operation') return <>{operationLabel(row.key)}</>
  if (group === 'round') return <Link to={`/rounds/${row.key}/summary`}>{s.round(row.key)}</Link>
  if (group === 'month') return <>{monthName(row.key)}</>
  if (row.key === 'unlinked') return <>{s.unlinked}</>
  if (row.key === 'voice') return <>{s.voiceRow}</>
  return (
    <span className="content">
      {plNbsp(row.label ?? row.key)}
    </span>
  )
}

export function UsagePage() {
  const [params, setParams] = useSearchParams()
  const group: UsageGroup = isGroup(params.get('group')) ? (params.get('group') as UsageGroup) : 'operation'
  const month = params.get('month') ?? undefined
  const [summary, setSummary] = useState<MonthSummary | null>(null)
  const [table, setTable] = useState<UsageTable | null>(null)
  const [prices, setPrices] = useState<PriceRow[]>([])
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchSummary(month), fetchUsage(group, month)])
      .then(([loadedSummary, loadedTable]) => {
        if (cancelled) return
        setSummary(loadedSummary)
        setTable(loadedTable)
        setError(false)
      })
      .catch(() => !cancelled && setError(true))
    return () => {
      cancelled = true
    }
  }, [group, month])

  useEffect(() => {
    fetchPrices()
      .then(setPrices)
      .catch(() => setPrices([]))
  }, [])

  const choose = (next: Record<string, string>) => {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) merged.set(key, value)
    setParams(merged)
  }

  const shown = summary?.month.month ?? month ?? ''
  const months = summary ? Array.from(new Set([shown, ...summary.months])).filter(Boolean) : []
  const state = summary?.month
  const percent = state ? percentUsed(state.spent_usd, state.ceiling_usd) : 0
  const blocked = table?.rows.reduce((sum, row) => sum + row.blocked, 0) ?? 0

  return (
    <>
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label={s.breadcrumb}>
            <Link to="/settings">{settingsStrings.title}</Link> <span aria-hidden="true">›</span>{' '}
            <Link to="/settings#spending">{settingsStrings.spending.title}</Link>{' '}
            <span aria-hidden="true">›</span> {s.crumb}
          </nav>
          <h1>{s.title}</h1>
          <p>{s.lead}</p>
        </div>
        {months.length > 0 && (
          <div className="field">
            <label className="label visually-hidden" htmlFor="month">
              {s.month}
            </label>
            <select
              className="select"
              id="month"
              value={shown}
              onChange={(event) => choose({ month: event.target.value })}
            >
              {months.map((value) => (
                <option key={value} value={value}>
                  {monthName(value)}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {error && <Notice kind="error">{s.loadError}</Notice>}

      {summary && state && (
        <>
          <h2 className="visually-hidden">{s.glance(monthName(state.month))}</h2>
          <div className="scores">
            <div>
              <p className="k">{s.thisMonth}</p>
              <p className="v num">{usd(state.spent_usd)}</p>
              {state.ceiling_usd > 0 ? (
                <>
                  <p className="of">{s.ofCeiling(usd(state.ceiling_usd))}</p>
                  <div className="meter-bar" role="img" aria-label={settingsStrings.spending.meter(percent)}>
                    <span style={{ width: `${percent}%` }} />
                  </div>
                </>
              ) : (
                <p className="of">{settingsStrings.spending.noCeiling}</p>
              )}
            </div>
            <div>
              <p className="k">{s.rounds}</p>
              <p className="v">{summary.rounds}</p>
              {summary.per_round_usd !== null && (
                <p className="of num">{s.perRound(usd(summary.per_round_usd))}</p>
              )}
            </div>
            <div>
              <p className="k">{s.library}</p>
              <p className="v measure">{usd(summary.library_usd)}</p>
              <p className="of">{s.libraryWhat}</p>
            </div>
            <div>
              <p className="k">{s.voice}</p>
              <p className="v measure">{usd(summary.voice_usd)}</p>
              <p className="of">
                {summary.voice_provider === 'openai'
                  ? settingsStrings.openaiVoice.voiceWhat
                  : s.voiceWhat(summary.voice_provider)}
              </p>
            </div>
          </div>
        </>
      )}

      <nav className="tabs mt-10" aria-label={s.groupBy}>
        {GROUPS.map((which) => (
          <Link
            key={which}
            to={{ search: `?${new URLSearchParams({ ...(month ? { month } : {}), group: which })}` }}
            aria-current={group === which ? 'page' : undefined}
          >
            {s.groups[which]}
          </Link>
        ))}
      </nav>

      {table && (
        <div className="table-wrap">
          <table className="table wide middle">
            <thead>
              <tr>
                <th>{s.columns[group]}</th>
                {group === 'operation' && <th>{s.model}</th>}
                <th className="r">{s.requests}</th>
                <th className="r">{s.amount}</th>
                <th className="r">{s.estimated}</th>
                <th className="r wrap">{s.reported}</th>
              </tr>
            </thead>
            <tbody>
              {table.rows.length === 0 && (
                <tr>
                  <td colSpan={group === 'operation' ? 6 : 5} className="muted">
                    {s.empty}
                  </td>
                </tr>
              )}
              {table.rows.map((row) => (
                <tr key={`${row.key}-${row.provider ?? ''}-${row.model ?? ''}`}>
                  <td>
                    <RowName row={row} group={group} />
                  </td>
                  {group === 'operation' && (
                    <td className="nowrap">
                      {providerLabel(row.provider ?? '')} · {row.model}
                      {!row.price_known && (
                        <>
                          <br />
                          <small className="muted">{s.priceUnknown}</small>
                        </>
                      )}
                    </td>
                  )}
                  <td className="r">
                    {row.requests}
                    {row.blocked > 0 && (
                      <>
                        <br />
                        <small className="muted">{s.blocked(row.blocked)}</small>
                      </>
                    )}
                  </td>
                  <td className="r nowrap">{amount(row)}</td>
                  <Money value={row.estimated_usd} />
                  <Money value={row.reported_usd} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {blocked > 0 && <p className="small mt-4">{s.blockedTotal(blocked)}</p>}
      <p className="small muted mt-4">
        {s.dashNote} <Link to="/progress/history">{s.roundsLink}</Link> {s.roundsOwn}
      </p>

      <section className="mt-10" aria-labelledby="how-h">
        <h2 id="how-h">{s.how}</h2>
        <p className="small">{s.howBody}</p>
        {prices.length > 0 && (
          <ul className="small">
            {prices.map((price) => (
              <li key={`${price.provider}-${price.model}`}>
                {s.priceLine(providerLabel(price.provider), price.model, priceText(price))}
              </li>
            ))}
          </ul>
        )}
        {prices.length > 0 && <p className="small muted">{s.checked(prices[0].checked)}</p>}
      </section>
    </>
  )
}

function priceText(price: PriceRow): string {
  if (price.audio_per_min) return s.perMinute(usd(price.audio_per_min))
  if (price.chars_per_m) return s.perChars(usd(price.chars_per_m))
  if (price.input_per_m || price.output_per_m)
    return s.perTokens(usd(price.input_per_m), usd(price.output_per_m))
  return s.free
}
