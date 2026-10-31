import { afterEach, describe, expect, it, vi } from 'vitest'
import { dateOnly, score } from '../rounds/feedback/format'
import {
  ariaValues,
  perMinuteAxis,
  scoreAxis,
  secondsAxis,
  segments,
  tradeoffIndex,
  viewWidth,
  washes,
  xPositions,
  yOf,
} from './trendGeometry'

describe('x positions', () => {
  it('matches the template for seven points and centres a single one', () => {
    expect(xPositions(7)).toEqual([60, 128.7, 197.3, 266, 334.7, 403.3, 472])
    expect(xPositions(1)).toEqual([266])
    expect(xPositions(0)).toEqual([])
  })

  it('widens the chart with a fixed step beyond seven points', () => {
    expect(viewWidth(7)).toBe(520)
    expect(viewWidth(12)).toBe(44 + 64 * 11 + 76)
    const xs = xPositions(12)
    expect(xs[1] - xs[0]).toBe(64)
    expect(xs).toHaveLength(12)
  })
})

describe('axes', () => {
  it('takes the template score axis for 45 to 69', () => {
    expect(scoreAxis([45, 69, null])).toEqual({ min: 40, max: 80, ticks: [40, 50, 60, 70, 80] })
  })

  it('is at least 40 wide and stays within 0 to 100', () => {
    expect(scoreAxis([2, 3])).toMatchObject({ min: 0, max: 40 })
    expect(scoreAxis([97, 99])).toMatchObject({ min: 60, max: 100 })
    expect(scoreAxis([null])).toMatchObject({ min: 0, max: 100 })
  })

  it('maps values to the template y positions', () => {
    const axis = scoreAxis([45, 69])
    expect(yOf(axis, 48)).toBe(152.8)
    expect(yOf(axis, 40)).toBe(184)
    expect(yOf(axis, 80)).toBe(28)
  })

  it('rounds the seconds axis up to a multiple of 5 s with five ticks', () => {
    expect(secondsAxis([19400])).toEqual({ min: 0, max: 20, ticks: [0, 5, 10, 15, 20] })
    expect(secondsAxis([null])).toMatchObject({ max: 5 })
  })

  it('rounds the per-minute axis up to a whole number, at least 3', () => {
    expect(perMinuteAxis([2.6, 1.4])).toEqual({ min: 0, max: 3, ticks: [0, 1, 2, 3] })
    expect(perMinuteAxis([3.2])).toMatchObject({ max: 4 })
    expect(perMinuteAxis([null])).toMatchObject({ max: 3 })
  })
})

describe('washes', () => {
  it('draws the stripes of the band table inside the axis only', () => {
    const axis = scoreAxis([45, 69])
    expect(washes(axis, [30, 60, 80])).toEqual([
      { band: 'needs', y: 106, height: 78, labelY: 178 },
      { band: 'good', y: 28, height: 78, labelY: 42 },
    ])
  })
})

describe('segments', () => {
  const xs = [60, 128.7, 197.3, 266]
  const y = (v: number) => 200 - v

  it('splits a line at a missing value and leaves a lone value without a line', () => {
    expect(segments([48, null, 55, 58], xs, y)).toEqual(['197.3,145 266,142'])
  })

  it('never asks for the y of a missing value', () => {
    const asked: number[] = []
    segments([null, 3, null], xs, (v) => {
      asked.push(v)
      return v
    })
    expect(asked).toEqual([3])
  })

  it('draws one polyline for a complete series', () => {
    expect(segments([10, 20], xs, y)).toEqual(['60,190 128.7,180'])
  })
})

describe('labels and the trade-off', () => {
  it('names a missing value as unavailable', () => {
    expect(ariaValues([48, null], score, 'unavailable')).toBe('48, unavailable')
    expect(ariaValues([null, 48], score, 'n/a')).toBe('n/a, 48')
  })

  it('does not call out the template data, where the drop is only 2 points', () => {
    const points = [
      { latencyMs: 15500, factual: 57 },
      { latencyMs: 10600, factual: 55 },
    ]
    expect(tradeoffIndex(points)).toBeNull()
  })

  it('finds the latest point that is sooner while the factual score fell', () => {
    const points = [
      { latencyMs: 19400, factual: 45 },
      { latencyMs: 15500, factual: 57 },
      { latencyMs: 10600, factual: 50 },
      { latencyMs: 10000, factual: null },
    ]
    expect(tradeoffIndex(points)).toBe(2)
  })

  it('needs a drop of 20% in time', () => {
    const points = [
      { latencyMs: 10000, factual: 70 },
      { latencyMs: 8500, factual: 50 },
    ]
    expect(tradeoffIndex(points)).toBeNull()
  })
})

describe('local days', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('labels a point in the viewer zone, not in UTC', () => {
    vi.stubEnv('TZ', 'Pacific/Auckland')
    expect(dateOnly('2026-10-21T20:30:00Z')).toBe('22 Oct')
    vi.stubEnv('TZ', 'UTC')
    expect(dateOnly('2026-10-21T20:30:00Z')).toBe('21 Oct')
  })
})
