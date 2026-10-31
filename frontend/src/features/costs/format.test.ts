import { describe, expect, it } from 'vitest'
import { aboutMinutes, monthName, percentUsed, thousands, usd, usdRange } from './format'

describe('cost formatting', () => {
  it('shows four decimals below one cent, else two', () => {
    expect(usd(0.0042)).toBe('$0.0042')
    expect(usd(2.4)).toBe('$2.40')
    expect(usd(0)).toBe('$0.00')
    expect(usd(15)).toBe('$15.00')
  })

  it('writes a range with an en dash, one value when the ends round equal', () => {
    expect(usdRange(0.45, 0.55)).toBe('$0.45–0.55')
    expect(usdRange(0.451, 0.454)).toBe('$0.45')
  })

  it('says about how many minutes', () => {
    expect(aboutMinutes(4.2)).toBe('about 4 minutes')
    expect(aboutMinutes(1)).toBe('about 1 minute')
    expect(aboutMinutes(0.2)).toBe('about 1 minute')
  })

  it('names months and shares', () => {
    expect(monthName('2026-10')).toBe('October 2026')
    expect(percentUsed(2.4, 15)).toBe(16)
    expect(percentUsed(20, 15)).toBe(100)
    expect(percentUsed(2, 0)).toBe(0)
    expect(thousands(612000)).toBe('612k')
    expect(thousands(4)).toBe('4')
  })
})
