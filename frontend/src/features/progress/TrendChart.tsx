import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { progressStrings } from './strings'
import {
  barHeight,
  isWide,
  num,
  PLOT_BOTTOM,
  PLOT_LEFT,
  plotRight,
  segments,
  viewWidth,
  xPositions,
  yOf,
  type Axis,
  type Value,
  type Wash,
} from './trendGeometry'

const t = progressStrings.trends

export interface ChartLine {
  values: Value[]
  axis: Axis
  /** "", "dash" or "dot soft": the line styles of the template tell series apart without colour. */
  style?: string
}

export interface ChartRound {
  id: number
  /** The label under the point ("22 Oct"). */
  date: string
  /** The title of the linked point on the first line, or null for a round without a value. */
  title: string | null
  /** The small number under the date, when the chart has them. */
  count?: number | null
}

export interface ChartMark {
  /** The mark stands between this round and the one before. */
  index: number
  text: string
}

export interface TrendChartProps {
  label: string
  rounds: ChartRound[]
  /** The left axis the first line is drawn on; its ticks are written with `format`. */
  axis: Axis
  format: (tick: number) => string
  /** Drawn first-to-last with the first line on top; only the first line carries linked points. */
  lines: ChartLine[]
  bars?: { values: Value[]; axis: Axis }
  rightAxis?: { axis: Axis; format: (tick: number) => string }
  washes?: Wash[]
  marks?: ChartMark[]
  note?: { index: number; text: string }
  /** Show the number under each date. */
  counts?: boolean
}

/** One chart of the Trends tab: the markup of docs/templates/trends.html, built from the data. */
export function TrendChart({
  label,
  rounds,
  axis,
  format,
  lines,
  bars,
  rightAxis,
  washes = [],
  marks = [],
  note,
  counts = false,
}: TrendChartProps) {
  const n = rounds.length
  const xs = xPositions(n)
  const wide = isWide(n)
  const width = viewWidth(n)
  const right = plotRight(n)
  const height = counts ? 228 : 214
  const box = useRef<HTMLDivElement>(null)

  // A long chart opens at the newest round. (Below 640px CSS does it with a right-to-left scroller.)
  useEffect(() => {
    const el = box.current
    if (el && wide && getComputedStyle(el).direction !== 'rtl') el.scrollLeft = el.scrollWidth
  }, [wide, n])

  const lone = (values: Value[], i: number) => values[i] !== null && values[i - 1] == null && values[i + 1] == null

  return (
    <>
      <div
        className={wide ? 'chart-scroll long' : 'chart-scroll'}
        tabIndex={0}
        role="group"
        aria-label={t.chartGroup}
        ref={box}
      >
        <svg
          className="trend wide"
          viewBox={`0 0 ${width} ${height}`}
          // "group", not "img": children of an img are presentational, and the round points are links
          role="group"
          aria-label={label}
          style={wide ? { minWidth: width } : undefined}
        >
          {washes.map((wash) => (
            <rect
              key={wash.band}
              className={`wash ${wash.band}`}
              x={PLOT_LEFT}
              y={wash.y}
              width={right - PLOT_LEFT}
              height={wash.height}
            />
          ))}
          {washes.map(
            (wash) =>
              wash.labelY !== null && (
                <text key={`l-${wash.band}`} className={`wash-lbl ${wash.band}`} x={50} y={wash.labelY}>
                  {progressStrings.bandWords[BAND_OF[wash.band]]}
                </text>
              ),
          )}
          {axis.ticks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={PLOT_LEFT} y1={yOf(axis, tick)} x2={right} y2={yOf(axis, tick)} />
              <text x={0} y={yOf(axis, tick) + 4}>
                {format(tick)}
              </text>
            </g>
          ))}
          {rightAxis?.axis.ticks.map((tick) => (
            <text key={`r-${tick}`} x={right + 6} y={yOf(rightAxis.axis, tick) + 4}>
              {rightAxis.format(tick)}
            </text>
          ))}
          {marks.map((mark) => {
            const x = num((xs[mark.index - 1] + xs[mark.index]) / 2)
            return (
              <g key={`m-${mark.index}`}>
                <line className="mark" x1={x} y1={22} x2={x} y2={PLOT_BOTTOM} />
                <text className="mk" x={Number(x) + 5} y={18}>
                  {mark.text}
                </text>
              </g>
            )
          })}
          {bars?.values.map(
            (value, i) =>
              value !== null && (
                <rect
                  key={`b-${rounds[i].id}`}
                  className="bar"
                  x={num(xs[i] - 11)}
                  y={yOf(bars.axis, value)}
                  width={22}
                  height={barHeight(bars.axis, value)}
                  rx={3}
                />
              ),
          )}
          {[...lines].reverse().flatMap((line, reversed) =>
            segments(line.values, xs, (v) => yOf(line.axis, v)).map((points, k) => (
              <polyline
                key={`p-${reversed}-${k}`}
                className={line.style ? `line ${line.style}` : 'line'}
                points={points}
              />
            )),
          )}
          {lines.slice(1).flatMap((line, k) =>
            line.values.map(
              (value, i) =>
                value !== null &&
                lone(line.values, i) && (
                  <circle
                    key={`o-${k}-${rounds[i].id}`}
                    className="pt"
                    cx={xs[i]}
                    cy={yOf(line.axis, value)}
                    r={3}
                    aria-hidden="true"
                  />
                ),
            ),
          )}
          {rounds.map((round, i) => {
            const value = lines[0]?.values[i] ?? null
            if (value === null || round.title === null) return null
            return (
              <Link key={round.id} to={`/rounds/${round.id}/summary`} aria-label={round.title}>
                <title>{round.title}</title>
                <circle
                  className={i === n - 1 ? 'pt this' : 'pt'}
                  cx={xs[i]}
                  cy={yOf(lines[0].axis, value)}
                  r={i === n - 1 ? 5 : 4}
                />
              </Link>
            )
          })}
          {note && (
            <text className="mk" x={num(xs[note.index] - 16)} y={150} textAnchor="end">
              {note.text}
            </text>
          )}
          {rounds.map((round, i) => (
            <g key={`x-${round.id}`}>
              <text className={i === n - 1 ? 'val' : undefined} x={xs[i]} y={204} textAnchor="middle">
                {round.date}
              </text>
              {counts && round.count !== undefined && (
                <text x={xs[i]} y={220} textAnchor="middle">
                  {round.count ?? ''}
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>
      {wide && <p className="chart-hint">{t.scrollHint}</p>}
    </>
  )
}

const BAND_OF = {
  weak: 'weak',
  needs: 'needs_improvement',
  good: 'good',
  'very-good': 'very_good',
} as const
