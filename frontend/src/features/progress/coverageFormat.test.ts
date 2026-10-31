import { describe, expect, it } from 'vitest'
import { KEY_ORDER, prep, recentFactual, STATUSES, statusClass } from './coverageFormat'
import { progressStrings } from './strings'

describe('coverage format', () => {
  it('maps a status to its colour class', () => {
    expect(statusClass('not_practised')).toBe('none')
    expect(statusClass('needs_work')).toBe('needs')
    expect(statusClass('improving')).toBe('improving')
    expect(statusClass('strong')).toBe('strong')
  })

  it('orders the statuses for the counts and for the key line', () => {
    expect(STATUSES).toEqual(['strong', 'improving', 'needs_work', 'not_practised'])
    expect(KEY_ORDER).toEqual(['not_practised', 'needs_work', 'improving', 'strong'])
  })

  it('joins the recent factual scores, rounded', () => {
    expect(recentFactual([84.4, 88])).toBe('84 · 88')
    expect(recentFactual([])).toBe('—')
  })

  it('shows the preparation in seconds and a dash, never "0 s", when missing', () => {
    expect(prep(8200)).toBe('Prep. 8 s')
    expect(prep(null)).toBe('—')
    expect(prep(undefined)).toBe('—')
  })

  it('has the four English labels', () => {
    expect(progressStrings.coverage.status).toEqual({
      not_practised: 'Not practised',
      needs_work: 'Needs work',
      improving: 'Improving',
      strong: 'Strong',
    })
  })
})
