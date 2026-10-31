/** Raw PCM helpers: a pre-roll ring buffer and a WAV encoder. Pure, no Web Audio. */

export class PcmRingBuffer {
  private readonly data: Float32Array
  private write = 0
  private filled = 0
  private total = 0

  constructor(sampleRate: number, seconds: number) {
    this.data = new Float32Array(Math.round(sampleRate * seconds))
  }

  /** Samples pushed since creation, including the ones already overwritten. */
  get totalSamples(): number {
    return this.total
  }

  push(chunk: Float32Array): void {
    const capacity = this.data.length
    this.total += chunk.length
    if (capacity === 0) return
    const tail = chunk.length > capacity ? chunk.subarray(chunk.length - capacity) : chunk
    const first = Math.min(tail.length, capacity - this.write)
    this.data.set(tail.subarray(0, first), this.write)
    this.data.set(tail.subarray(first), 0)
    this.write = (this.write + tail.length) % capacity
    this.filled = Math.min(capacity, this.filled + tail.length)
  }

  /** A chronological copy of the buffered samples. */
  snapshot(): Float32Array {
    const out = new Float32Array(this.filled)
    const start = (this.write - this.filled + this.data.length) % (this.data.length || 1)
    const first = Math.min(this.filled, this.data.length - start)
    out.set(this.data.subarray(start, start + first), 0)
    out.set(this.data.subarray(0, this.filled - first), first)
    return out
  }
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i))
}

/** 16-bit PCM mono WAV bytes; samples are clipped to ±1.0. */
export function encodeWavBytes(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const dataBytes = samples.length * 2
  const buffer = new ArrayBuffer(44 + dataBytes)
  const view = new DataView(buffer)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataBytes, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, dataBytes, true)
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, Math.round(s < 0 ? s * 0x8000 : s * 0x7fff), true)
  }
  return buffer
}

export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  return new Blob([encodeWavBytes(samples, sampleRate)], { type: 'audio/wav' })
}
