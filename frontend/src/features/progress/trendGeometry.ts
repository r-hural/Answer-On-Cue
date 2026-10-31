/** Chart geometry for the Trends tab (not trendChart.ts: that name collides with TrendChart.tsx on case-insensitive disks) (pure). The numbers are those of docs/templates/trends.html. */

export type Value = number | null

export const PLOT_TOP = 28
export const PLOT_BOTTOM = 184
export const PLOT_LEFT = 44
const FIRST_X = 60
const SPREAD = 412 // first to last point when the chart is not scrolled
const WIDE_FROM = 8 // points; more than 7 get a fixed step and a scrolling box
const STEP = 64
const BASE_WIDTH = 520

export const round1 = (value: number) => Math.round(value * 10) / 10

/** "128.7", "60": at most one decimal, no trailing ".0". */
export const num = (value: number) => String(round1(value))

export function isWide(n: number): boolean {
  return n >= WIDE_FROM
}

export function xPositions(n: number): number[] {
  if (n <= 0) return []
  if (n === 1) return [266]
  const step = isWide(n) ? STEP : SPREAD / (n - 1)
  return Array.from({ length: n }, (_, i) => round1(FIRST_X + i * step))
}

export function viewWidth(n: number): number {
  return isWide(n) ? 44 + STEP * (n - 1) + 76 : BASE_WIDTH
}

/** Right end of the grid lines. */
export function plotRight(n: number): number {
  return viewWidth(n) - 32
}

export interface Axis {
  min: number
  max: number
  ticks: number[]
}

const present = (values: Value[]): number[] => values.filter((v): v is number => v !== null)

/** A 0–100 score axis: 5 points of air around the data, whole tens, at least 40 wide. */
export function scoreAxis(values: Value[]): Axis {
  const have = present(values)
  let min = 0
  let max = 100
  if (have.length > 0) {
    min = Math.max(0, Math.floor((Math.min(...have) - 5) / 10) * 10)
    max = Math.min(100, Math.ceil((Math.max(...have) + 5) / 10) * 10)
    if (max - min < 40) {
      max = Math.min(100, min + 40)
      min = max - 40
    }
  }
  const ticks: number[] = []
  for (let tick = min; tick <= max; tick += 10) ticks.push(tick)
  return { min, max, ticks }
}

/** Seconds from milliseconds: 0 up to the next multiple of 5 s, five ticks. */
export function secondsAxis(ms: Value[]): Axis {
  const have = present(ms)
  const top = have.length > 0 ? Math.max(...have) / 1000 : 0
  const max = Math.max(5, Math.ceil(top / 5) * 5)
  return { min: 0, max, ticks: [0, 1, 2, 3, 4].map((i) => (max / 4) * i) }
}

/** A per-minute axis: 0 up to the next whole number, at least 3. */
export function perMinuteAxis(values: Value[]): Axis {
  const have = present(values)
  const max = Math.max(3, Math.ceil(have.length > 0 ? Math.max(...have) : 0))
  const step = max <= 6 ? 1 : Math.ceil(max / 5)
  const top = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let tick = 0; tick <= top; tick += step) ticks.push(tick)
  return { min: 0, max: top, ticks }
}

/** The right axis of bars: a percentage, 0–100. */
export const PERCENT_AXIS: Axis = { min: 0, max: 100, ticks: [0, 25, 50, 75, 100] }

export function yOf(axis: Axis, value: number): number {
  const ratio = (value - axis.min) / (axis.max - axis.min)
  return round1(PLOT_BOTTOM - ratio * (PLOT_BOTTOM - PLOT_TOP))
}

/** Height of a bar of `value` on the axis. */
export function barHeight(axis: Axis, value: number): number {
  return round1(PLOT_BOTTOM - yOf(axis, value))
}

/**
 * The polylines of a series: a missing value ends a run, a run of one has no line (it is drawn as a
 * lone point). Nothing is ever computed for a missing value.
 */
export function segments(values: Value[], xs: number[], y: (value: number) => number): string[] {
  const runs: string[][] = []
  let run: string[] = []
  values.forEach((value, i) => {
    if (value === null) {
      if (run.length > 0) runs.push(run)
      run = []
      return
    }
    run.push(`${num(xs[i])},${num(y(value))}`)
  })
  if (run.length > 0) runs.push(run)
  return runs.filter((r) => r.length >= 2).map((r) => r.join(' '))
}

/** "48, unavailable" for the aria-label of a chart; `unavailable` is progressStrings.trends.unavailable. */
export function ariaValues(values: Value[], format: (value: number) => string, unavailable: string): string {
  return values.map((v) => (v === null ? unavailable : format(v))).join(', ')
}

export interface TradeoffPoint {
  latencyMs: Value
  factual: Value
}

export const FASTER_BY = 0.2
export const FACTUAL_FELL_BY = 5

/**
 * The latest point that came ≥20% sooner than the one before while the factual score fell by ≥5
 * (smaller differences of AI scores mean nothing). null when there is none.
 */
export function tradeoffIndex(points: TradeoffPoint[]): number | null {
  for (let i = points.length - 1; i >= 1; i--) {
    const [before, now] = [points[i - 1], points[i]]
    if (before.latencyMs === null || now.latencyMs === null) continue
    if (before.factual === null || now.factual === null) continue
    if (now.latencyMs <= before.latencyMs * (1 - FASTER_BY) && before.factual - now.factual >= FACTUAL_FELL_BY) {
      return i
    }
  }
  return null
}

export type WashBand = 'weak' | 'needs' | 'good' | 'very-good'

export interface Wash {
  band: WashBand
  y: number
  height: number
  /** Where the band's name sits, or null when the stripe is too thin to name. */
  labelY: number | null
}

/** The band stripes of the current band table that fall inside the axis. */
export function washes(axis: Axis, bounds: [number, number, number] | readonly number[]): Wash[] {
  const edges: [WashBand, number, number][] = [
    ['weak', 0, bounds[0]],
    ['needs', bounds[0], bounds[1]],
    ['good', bounds[1], bounds[2]],
    ['very-good', bounds[2], 100],
  ]
  const out: Wash[] = []
  for (const [band, from, to] of edges) {
    const low = Math.max(from, axis.min)
    const high = Math.min(to, axis.max)
    if (high <= low) continue
    const y = yOf(axis, high)
    const height = round1(yOf(axis, low) - y)
    const labelY = height < 18 ? null : band === 'good' || band === 'very-good' ? round1(y + 14) : round1(y + height - 6)
    out.push({ band, y, height, labelY })
  }
  return out
}
