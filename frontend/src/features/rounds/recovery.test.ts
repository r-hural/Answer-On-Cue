import { describe, expect, it } from 'vitest'
import { encodeWavBytes } from '../recording/pcm'
import { remainingMs, trimWav, trimWavBytes } from './recovery'

const RATE = 16000

function samplesOf(bytes: ArrayBuffer): number {
  return new DataView(bytes).getUint32(40, true) / 2
}

describe('trimWavBytes', () => {
  it('cuts a 16 kHz mono WAV to the given length with a valid header', () => {
    const wav = encodeWavBytes(new Float32Array(3 * RATE).fill(0.25), RATE)
    const cut = trimWavBytes(wav, 2000)
    const view = new DataView(cut)
    expect(samplesOf(cut)).toBe(32000)
    expect(cut.byteLength).toBe(44 + 64000)
    expect(String.fromCharCode(...new Uint8Array(cut, 0, 4))).toBe('RIFF')
    expect(view.getUint32(4, true)).toBe(36 + 64000)
    expect(String.fromCharCode(...new Uint8Array(cut, 36, 4))).toBe('data')
    expect(view.getInt16(44, true)).toBe(new DataView(wav).getInt16(44, true))
  })

  it('leaves anything else unchanged', () => {
    const notWav = new Uint8Array([1, 2, 3, 4, 5]).buffer
    expect(trimWavBytes(notWav, 1000)).toBe(notWav)
    const short = encodeWavBytes(new Float32Array(RATE), RATE)
    expect(trimWavBytes(short, 2000)).toBe(short)
    const otherRate = encodeWavBytes(new Float32Array(3 * 8000), 8000)
    expect(trimWavBytes(otherRate, 1000)).toBe(otherRate)
  })

  it('trimWav works on a Blob', async () => {
    const blob = new Blob([encodeWavBytes(new Float32Array(3 * RATE), RATE)], { type: 'audio/wav' })
    const cut = await trimWav(blob, 1000)
    expect(cut.size).toBe(44 + 32000)
    expect(cut.type).toBe('audio/wav')
  })
})

describe('remainingMs', () => {
  it('is the smallest known remainder, ignoring unknown clocks', () => {
    const deadlines = { perfDeadline: 10_000, wallDeadline: 1_000_010_000, serverDeadline: null, offsetMs: 0 }
    expect(remainingMs(deadlines, 4_000, 1_000_000_000)).toBe(6_000)
    expect(remainingMs(deadlines, 4_000, 1_000_008_000)).toBe(2_000)
    expect(remainingMs({ ...deadlines, perfDeadline: null }, 9_999, 1_000_001_000)).toBe(9_000)
  })

  it('reads a server deadline through the server offset', () => {
    // the server clock runs 5 s ahead: its deadline 2_000_007_000 is 2 s from local 2_000_000_000
    const deadlines = { perfDeadline: 50_000, wallDeadline: null, serverDeadline: 2_000_007_000, offsetMs: 5_000 }
    expect(remainingMs(deadlines, 0, 2_000_000_000)).toBe(2_000)
  })

  it('never goes below zero, and is infinite when nothing is known', () => {
    expect(remainingMs({ perfDeadline: 1_000, wallDeadline: null, serverDeadline: null, offsetMs: 0 }, 5_000, 0)).toBe(0)
    expect(remainingMs({ perfDeadline: null, wallDeadline: null, serverDeadline: null, offsetMs: 0 }, 0, 0)).toBe(Infinity)
  })
})
