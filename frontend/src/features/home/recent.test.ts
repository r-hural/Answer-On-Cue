import { describe, expect, it } from 'vitest'
import type { RoundListItem } from './api'
import { formatWhen, lastPractised, mergeRecent, roundHref, unfinished } from './recent'

function round(id: number, started: string, status = 'finished'): RoundListItem {
  return {
    id,
    status,
    started_at: started,
    finished_at: null,
    total: 5,
    done: 2,
    feedback_timing: 'practice',
  }
}

const perSubject = [
  {
    subject: { id: 1, name: 'Historia' },
    rounds: [
      round(1, '2026-10-01T10:00:00Z'),
      round(2, '2026-10-05T10:00:00Z'),
      round(3, '2026-10-09T10:00:00Z', 'paused'),
    ],
  },
  {
    subject: { id: 2, name: 'Biologia' },
    rounds: [
      round(4, '2026-10-03T10:00:00Z'),
      round(5, '2026-10-07T10:00:00Z', 'active'),
      round(6, '2026-10-11T10:00:00Z'),
      round(7, '2026-10-12T10:00:00Z', 'abandoned'),
    ],
  },
]

describe('recent rounds', () => {
  it('merges the 5 newest with their subject', () => {
    const merged = mergeRecent(perSubject)
    expect(merged.map((r) => r.id)).toEqual([7, 6, 3, 5, 2])
    expect(merged[0]).toMatchObject({ subjectId: 2, subjectName: 'Biologia' })
    expect(merged[2]).toMatchObject({ subjectId: 1, subjectName: 'Historia' })
  })

  it('finds the newest unfinished round, or null', () => {
    expect(unfinished(perSubject)?.id).toBe(3)
    const calm = [{ subject: { id: 1, name: 'H' }, rounds: [round(1, '2026-10-01T10:00:00Z')] }]
    expect(unfinished(calm)).toBeNull()
  })

  it('formats the start as day, month and time', () => {
    expect(formatWhen('2026-10-21T17:10:00Z')).toMatch(/^\d{1,2} [A-Z][a-z]{2}, \d{2}:\d{2}$/)
  })

  it('names the day of the newest round of a subject', () => {
    expect(lastPractised([])).toBeNull()
    expect(lastPractised(perSubject[0].rounds)).toMatch(/^\d{1,2} Oct$/)
  })

  it('links unfinished rounds back into the round and others to the summary', () => {
    expect(roundHref({ id: 8, status: 'paused' })).toBe('/rounds/8')
    expect(roundHref({ id: 9, status: 'finished' })).toBe('/rounds/9/summary')
  })
})
