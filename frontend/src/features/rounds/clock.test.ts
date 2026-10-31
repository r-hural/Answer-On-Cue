import { describe, expect, it } from 'vitest'
import { formatClock, isLow, questionSize, remainingMs } from './clock'

describe('clock', () => {
  it('never goes below zero', () => {
    expect(remainingMs(1000, 400)).toBe(600)
    expect(remainingMs(1000, 1000)).toBe(0)
    expect(remainingMs(1000, 5000)).toBe(0)
  })

  it('formats m:ss and rounds partial seconds up', () => {
    expect(formatClock(94_000)).toBe('1:34')
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(93_100)).toBe('1:34')
    expect(formatClock(1)).toBe('0:01')
    expect(formatClock(600_000)).toBe('10:00')
    expect(formatClock(-5)).toBe('0:00')
  })

  it('is low in the last 20 seconds, or a quarter of a short answer', () => {
    expect(isLow(20_000, 120_000)).toBe(true)
    expect(isLow(20_001, 120_000)).toBe(false)
    expect(isLow(15_000, 60_000)).toBe(true)
    expect(isLow(16_000, 60_000)).toBe(false)
    expect(isLow(5_000, 20_000)).toBe(true)
    expect(isLow(6_000, 20_000)).toBe(false)
  })

  it('sizes a question by its length', () => {
    expect(questionSize('a'.repeat(120))).toBe('short')
    expect(questionSize('a'.repeat(121))).toBe('medium')
    expect(questionSize('a'.repeat(240))).toBe('medium')
    expect(questionSize('a'.repeat(241))).toBe('long')
  })

  it('follows the deadline when the tab was suspended for 30 seconds', () => {
    const start = 10_000
    const deadline = start + 120_000
    expect(formatClock(remainingMs(deadline, start + 5_000))).toBe('1:55')
    // no ticks ran for 30 s; the next one still shows the true remaining time
    expect(formatClock(remainingMs(deadline, start + 35_000))).toBe('1:25')
  })
})
