import { describe, expect, it } from 'vitest'
import { PcmRingBuffer, encodeWav, encodeWavBytes } from './pcm'

function ramp(start: number, length: number): Float32Array {
  const chunk = new Float32Array(length)
  for (let i = 0; i < length; i++) chunk[i] = start + i
  return chunk
}

function ascii(view: DataView, offset: number, length: number): string {
  let text = ''
  for (let i = 0; i < length; i++) text += String.fromCharCode(view.getUint8(offset + i))
  return text
}

describe('PcmRingBuffer', () => {
  it('keeps exactly the last second, in chronological order', () => {
    const ring = new PcmRingBuffer(16000, 1)
    for (let pushed = 0; pushed < 40000; pushed += 320) ring.push(ramp(pushed, 320))
    const snap = ring.snapshot()
    expect(snap.length).toBe(16000)
    expect(snap[0]).toBe(24000)
    expect(snap[15999]).toBe(39999)
    for (let i = 1; i < snap.length; i++) expect(snap[i] - snap[i - 1]).toBe(1)
    expect(ring.totalSamples).toBe(40000)
  })

  it('returns only what was pushed before it fills', () => {
    const ring = new PcmRingBuffer(16000, 1)
    ring.push(ramp(0, 500))
    expect(Array.from(ring.snapshot())).toEqual(Array.from(ramp(0, 500)))
    expect(new PcmRingBuffer(16000, 1).snapshot().length).toBe(0)
  })

  it('handles a chunk larger than the buffer', () => {
    const ring = new PcmRingBuffer(10, 1)
    ring.push(ramp(0, 25))
    expect(Array.from(ring.snapshot())).toEqual(Array.from(ramp(15, 10)))
  })

  it('returns a copy', () => {
    const ring = new PcmRingBuffer(10, 1)
    ring.push(ramp(0, 5))
    ring.snapshot()[0] = 99
    expect(ring.snapshot()[0]).toBe(0)
  })
})

describe('encodeWav', () => {
  it('writes a 16-bit PCM mono header', () => {
    const view = new DataView(encodeWavBytes(new Float32Array(100), 16000))
    expect(view.byteLength).toBe(44 + 200)
    expect(ascii(view, 0, 4)).toBe('RIFF')
    expect(view.getUint32(4, true)).toBe(36 + 200)
    expect(ascii(view, 8, 4)).toBe('WAVE')
    expect(ascii(view, 12, 4)).toBe('fmt ')
    expect(view.getUint32(16, true)).toBe(16)
    expect(view.getUint16(20, true)).toBe(1)
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(16000)
    expect(view.getUint32(28, true)).toBe(32000)
    expect(view.getUint16(32, true)).toBe(2)
    expect(view.getUint16(34, true)).toBe(16)
    expect(ascii(view, 36, 4)).toBe('data')
    expect(view.getUint32(40, true)).toBe(200)
  })

  it('converts and clips samples at ±1.0', () => {
    const view = new DataView(encodeWavBytes(new Float32Array([0, 0.5, -0.5, 1, -1, 1.7, -3]), 16000))
    const samples = Array.from({ length: 7 }, (_, i) => view.getInt16(44 + i * 2, true))
    expect(samples).toEqual([0, 16384, -16384, 32767, -32768, 32767, -32768])
  })

  it('returns an audio/wav blob', () => {
    const blob = encodeWav(new Float32Array(10), 16000)
    expect(blob.type).toBe('audio/wav')
    expect(blob.size).toBe(64)
  })
})
