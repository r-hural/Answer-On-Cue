/** Money and time as the cost screens show them. */

/** "$2.40"; below one cent four decimals ("$0.0042"), so a small cost never reads as $0.00. */
export function usd(value: number): string {
  const amount = Math.max(value, 0)
  return amount > 0 && amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`
}

/** "$0.45–0.55"; one value when both ends round the same. */
export function usdRange(low: number, high: number): string {
  const from = usd(low)
  const to = usd(high)
  return from === to ? from : `${from}–${to.slice(1)}`
}

export function aboutMinutes(minutes: number): string {
  const rounded = Math.max(1, Math.round(minutes))
  return `about ${rounded} ${rounded === 1 ? 'minute' : 'minutes'}`
}

/** Share of the ceiling used, 0–100 (whole percent), for the meter. */
export function percentUsed(spent: number, ceiling: number): number {
  if (ceiling <= 0) return 0
  return Math.min(100, Math.round((spent / ceiling) * 100))
}

/** "October 2026" for "2026-10". */
export function monthName(month: string): string {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number - 1, 1)).toLocaleString('en-GB', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** "612k" for 612 000, "4" for 4. */
export function thousands(value: number): string {
  return value >= 1000 ? `${Math.round(value / 1000)}k` : String(value)
}
