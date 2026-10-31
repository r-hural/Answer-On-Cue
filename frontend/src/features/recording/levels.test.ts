import { describe, expect, it } from 'vitest'
import { DEFAULT_TRIGGER, SpeechTrigger, calibrateNoiseFloor, rmsDb } from './levels'

const FLOOR = -60
const FRAME = DEFAULT_TRIGGER.frameMs

/** Pushes `count` frames at `level`, starting at `startMs`; returns every non-null result. */
function feed(trigger: SpeechTrigger, level: number, count: number, startMs: number): number[] {
  const hits: number[] = []
  for (let i = 0; i < count; i++) {
    const onset = trigger.push(level, startMs + i * FRAME)
    if (onset !== null) hits.push(onset)
  }
  return hits
}

describe('rmsDb', () => {
  it('is -120 for silence and an empty frame', () => {
    expect(rmsDb(new Float32Array(320))).toBe(-120)
    expect(rmsDb(new Float32Array(0))).toBe(-120)
  })

  it('is about -3 dB for a full-scale sine', () => {
    const frame = new Float32Array(1600)
    for (let i = 0; i < frame.length; i++) frame[i] = Math.sin((2 * Math.PI * 440 * i) / 16000)
    expect(rmsDb(frame)).toBeCloseTo(-3.01, 1)
  })

  it('never goes below -120', () => {
    expect(rmsDb(new Float32Array([1e-9, -1e-9]))).toBe(-120)
  })
})

describe('calibrateNoiseFloor', () => {
  it('takes the 80th percentile', () => {
    expect(calibrateNoiseFloor([10, 1, 9, 2, 8, 3, 7, 4, 6, 5])).toBe(8)
    expect(calibrateNoiseFloor([-70, -70, -70, -70, -20])).toBe(-70)
  })

  it('is -120 without frames', () => {
    expect(calibrateNoiseFloor([])).toBe(-120)
  })
})

describe('SpeechTrigger', () => {
  it('has the documented defaults', () => {
    expect(DEFAULT_TRIGGER).toEqual({ marginDb: 12, minSpeechMs: 300, frameMs: 20, hangoverMs: 100 })
  })

  it('ignores a 120 ms burst 20 dB above the floor', () => {
    const trigger = new SpeechTrigger(FLOOR)
    expect(feed(trigger, FLOOR + 20, 6, 0)).toEqual([])
    expect(feed(trigger, FLOOR, 50, 120)).toEqual([])
  })

  it('ignores bursts spaced further apart than the hangover', () => {
    const trigger = new SpeechTrigger(FLOOR)
    for (let k = 0; k < 10; k++) {
      expect(feed(trigger, FLOOR + 20, 6, k * 400)).toEqual([])
      expect(feed(trigger, FLOOR, 14, k * 400 + 120)).toEqual([])
    }
  })

  it('triggers once on 400 ms of speech, at 300 ms, with onset at the first frame', () => {
    const trigger = new SpeechTrigger(FLOOR)
    feed(trigger, FLOOR, 25, 0)
    const results: Array<number | null> = []
    for (let i = 0; i < 20; i++) results.push(trigger.push(FLOOR + 20, 500 + i * FRAME))
    expect(results.filter((r) => r !== null)).toEqual([500])
    // 15 frames of 20 ms = 300 ms of speech confirm it.
    expect(results.indexOf(500)).toBe(14)
  })

  it('bridges short dips within the hangover', () => {
    const trigger = new SpeechTrigger(FLOOR)
    expect(feed(trigger, FLOOR + 20, 8, 0)).toEqual([])
    expect(feed(trigger, FLOOR, 4, 160)).toEqual([])
    expect(feed(trigger, FLOOR + 20, 8, 240)).toEqual([0])
  })

  it('does not trigger at floor + 11 dB (margin 12)', () => {
    const trigger = new SpeechTrigger(FLOOR)
    expect(feed(trigger, FLOOR + 11, 100, 0)).toEqual([])
    expect(feed(trigger, FLOOR + 12, 15, 2000)).toEqual([2000])
  })

  it('does not trigger again after the first trigger', () => {
    const trigger = new SpeechTrigger(FLOOR)
    expect(feed(trigger, FLOOR + 20, 20, 0)).toEqual([0])
    feed(trigger, FLOOR, 50, 400)
    expect(feed(trigger, FLOOR + 30, 50, 1400)).toEqual([])
  })

  it('never triggers while muted, and starts the count fresh after unmuting', () => {
    const trigger = new SpeechTrigger(FLOOR)
    expect(feed(trigger, FLOOR + 20, 10, 0)).toEqual([])
    trigger.setMuted(true)
    expect(feed(trigger, FLOOR + 30, 200, 200)).toEqual([])
    trigger.setMuted(false)
    // 200 ms before muting must not count: 10 more frames are not enough.
    expect(feed(trigger, FLOOR + 20, 10, 4200)).toEqual([])
    expect(feed(trigger, FLOOR + 20, 5, 4400)).toEqual([4200])
  })

  it('accepts a custom config', () => {
    const trigger = new SpeechTrigger(FLOOR, { marginDb: 6, minSpeechMs: 100, frameMs: 10, hangoverMs: 50 })
    const hits: number[] = []
    for (let i = 0; i < 10; i++) {
      const onset = trigger.push(FLOOR + 7, i * 10)
      if (onset !== null) hits.push(onset)
    }
    expect(hits).toEqual([0])
  })
})
