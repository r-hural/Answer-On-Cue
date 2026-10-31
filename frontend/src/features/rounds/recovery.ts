/** Helpers for answers that outlive a sleeping tab: deadlines from three clocks, and WAV trimming. */

export interface Deadlines {
  /** On the page clock (performance.now()). */
  perfDeadline: number | null
  /** On the wall clock (Date.now()), which keeps running while the computer sleeps. */
  wallDeadline: number | null
  /** On the server's clock (epoch ms), read through offsetMs. */
  serverDeadline: number | null
  /** Server clock minus the local wall clock. */
  offsetMs: number
}

/** The smallest remainder of the clocks that know a deadline (Infinity when none does). */
export function remainingMs(deadlines: Deadlines, perfNow: number, wallNow: number): number {
  const left: number[] = []
  if (deadlines.perfDeadline !== null) left.push(deadlines.perfDeadline - perfNow)
  if (deadlines.wallDeadline !== null) left.push(deadlines.wallDeadline - wallNow)
  if (deadlines.serverDeadline !== null) left.push(deadlines.serverDeadline - (wallNow + deadlines.offsetMs))
  return left.length === 0 ? Infinity : Math.max(0, Math.min(...left))
}

const HEADER_BYTES = 44
const SAMPLE_RATE = 16000

function ascii(view: DataView, offset: number, length: number): string {
  let text = ''
  for (let i = 0; i < length; i++) text += String.fromCharCode(view.getUint8(offset + i))
  return text
}

/**
 * The first `maxMs` of a 16-bit mono 16 kHz WAV as written by recording/pcm.ts (a 44-byte header);
 * any other buffer, or one already short enough, comes back unchanged.
 */
export function trimWavBytes(bytes: ArrayBuffer, maxMs: number): ArrayBuffer {
  if (bytes.byteLength < HEADER_BYTES) return bytes
  const view = new DataView(bytes)
  const plain =
    ascii(view, 0, 4) === 'RIFF' &&
    ascii(view, 8, 4) === 'WAVE' &&
    ascii(view, 36, 4) === 'data' &&
    view.getUint16(20, true) === 1 &&
    view.getUint16(22, true) === 1 &&
    view.getUint32(24, true) === SAMPLE_RATE &&
    view.getUint16(34, true) === 16
  if (!plain) return bytes
  const dataBytes = Math.min(view.getUint32(40, true), bytes.byteLength - HEADER_BYTES)
  const keep = Math.round((Math.max(0, maxMs) * SAMPLE_RATE) / 1000) * 2
  if (keep >= dataBytes) return bytes
  const out = bytes.slice(0, HEADER_BYTES + keep)
  const header = new DataView(out)
  header.setUint32(4, 36 + keep, true)
  header.setUint32(40, keep, true)
  return out
}

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(blob)
  })
}

export async function trimWav(blob: Blob, maxMs: number): Promise<Blob> {
  const bytes = await readBlob(blob)
  const cut = trimWavBytes(bytes, maxMs)
  return cut === bytes ? blob : new Blob([cut], { type: blob.type || 'audio/wav' })
}
