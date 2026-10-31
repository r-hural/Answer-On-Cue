import { describe, expect, it } from 'vitest'
import { roundTypeLabel } from './roundType'

describe('roundTypeLabel', () => {
  it('names the three round types; anything else is Standard', () => {
    expect(roundTypeLabel('standard')).toBe('Standard')
    expect(roundTypeLabel('adaptive')).toBe('Adaptive')
    expect(roundTypeLabel('extension')).toBe('Adaptive extension')
    expect(roundTypeLabel(undefined)).toBe('Standard')
  })
})
