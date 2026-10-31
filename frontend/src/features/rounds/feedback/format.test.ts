import { describe, expect, it } from 'vitest'
import { bandClass, bandKey, metricOrUnavailable, pauseSeconds, position, seconds, seconds1, timelineMarks } from './format'

describe('seconds', () => {
  it('rounds to whole seconds and keeps a decimal under one second', () => {
    expect(seconds(8200)).toBe('8 s')
    expect(seconds(300)).toBe('0.3 s')
    expect(seconds(0)).toBe('0 s')
    expect(seconds(21_000)).toBe('21 s')
  })

  it('seconds1 trims a trailing zero and pauseSeconds never does', () => {
    expect(seconds1(800)).toBe('0.8 s')
    expect(seconds1(3800)).toBe('3.8 s')
    expect(seconds1(0)).toBe('0 s')
    expect(seconds1(2000)).toBe('2 s')
    expect(pauseSeconds(2000)).toBe('2.0 s')
    expect(pauseSeconds(4100)).toBe('4.1 s')
  })

  it('position is m:ss of whole seconds', () => {
    expect(position(41_900)).toBe('0:41')
    expect(position(112_000)).toBe('1:52')
    expect(position(-5)).toBe('0:00')
  })
})

describe('metricOrUnavailable', () => {
  it('names the reason and never prints a zero for a missing value', () => {
    expect(metricOrUnavailable(null, 'no_speech')).toBe('unavailable (no speech)')
    expect(metricOrUnavailable(null, 'no_transcript')).toBe('unavailable (no transcript)')
    expect(metricOrUnavailable(null)).toBe('unavailable')
    expect(metricOrUnavailable(null, 'some_new_reason')).toBe('unavailable (some new reason)')
    expect(metricOrUnavailable(null, 'no_speech', seconds)).not.toMatch(/0/)
  })

  it('formats a present value, including a real zero', () => {
    expect(metricOrUnavailable(118, undefined, (value) => `${value} wpm`)).toBe('118 wpm')
    expect(metricOrUnavailable(0, undefined, seconds1)).toBe('0 s')
  })
})

describe('bands', () => {
  it('maps a band to the template classes', () => {
    expect(bandClass('needs_improvement')).toBe('band needs')
    expect(bandClass('very_good')).toBe('band very-good')
    expect(bandKey('weak')).toBe('weak')
    expect(bandKey('very_good')).toBe('very-good')
  })
})

describe('timelineMarks', () => {
  it('turns pauses into percentages of the recording, the mark in the middle, seeking one second early', () => {
    const marks = timelineMarks(
      [
        { start_ms: 41_000, end_ms: 45_100, kind: 'long' },
        { start_ms: 500, end_ms: 2700, kind: 'long' },
      ],
      112_000,
    )
    expect(marks[0]).toMatchObject({ left: 36.6, width: 3.7, center: 38.4, seekMs: 40_000, position: '0:41', length: '4.1 s' })
    expect(marks[1].seekMs).toBe(0)
    expect(marks[1].left).toBeGreaterThanOrEqual(0)
  })

  it('has no marks without a duration', () => {
    expect(timelineMarks([{ start_ms: 1, end_ms: 3000, kind: 'long' }], 0)).toEqual([])
  })
})
