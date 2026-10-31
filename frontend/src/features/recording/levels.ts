/** Frame levels and energy-based speech-start detection. Pure, no Web Audio. */

export const SILENCE_DB = -120

/** RMS level of a frame in dBFS; -120 for silence. */
export function rmsDb(frame: Float32Array): number {
  if (frame.length === 0) return SILENCE_DB
  let sum = 0
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i]
  const meanSquare = sum / frame.length
  if (meanSquare <= 0) return SILENCE_DB
  return Math.max(SILENCE_DB, 10 * Math.log10(meanSquare))
}

/** Noise floor: the 80th percentile (nearest rank) of the given frame levels. */
export function calibrateNoiseFloor(framesDb: number[]): number {
  if (framesDb.length === 0) return SILENCE_DB
  const sorted = [...framesDb].sort((a, b) => a - b)
  return sorted[Math.max(0, Math.ceil(0.8 * sorted.length) - 1)]
}

export interface TriggerConfig {
  /** A frame counts as speech when it is at least this far above the noise floor. */
  marginDb: number
  /** Speech frames needed (summed) before the start is confirmed. */
  minSpeechMs: number
  frameMs: number
  /** Quiet time a candidate survives; longer quiet starts the count again. */
  hangoverMs: number
}

export const DEFAULT_TRIGGER: TriggerConfig = { marginDb: 12, minSpeechMs: 300, frameMs: 20, hangoverMs: 100 }

export class SpeechTrigger {
  readonly noiseFloorDb: number
  private readonly cfg: TriggerConfig
  private muted = false
  private fired = false
  private onsetMs: number | null = null
  private lastLoudMs = 0
  private speechMs = 0

  constructor(noiseFloorDb: number, cfg: TriggerConfig = DEFAULT_TRIGGER) {
    this.noiseFloorDb = noiseFloorDb
    this.cfg = cfg
  }

  get thresholdDb(): number {
    return this.noiseFloorDb + this.cfg.marginDb
  }

  /** Returns the speech onset time (ms) once, when sustained speech is confirmed; otherwise null. */
  push(levelDb: number, tMs: number): number | null {
    if (this.fired || this.muted) return null
    if (levelDb >= this.thresholdDb) {
      if (this.onsetMs === null) {
        this.onsetMs = tMs
        this.speechMs = 0
      }
      this.speechMs += this.cfg.frameMs
      this.lastLoudMs = tMs
      if (this.speechMs >= this.cfg.minSpeechMs) {
        this.fired = true
        return this.onsetMs
      }
    } else if (this.onsetMs !== null && tMs - this.lastLoudMs > this.cfg.hangoverMs) {
      this.reset()
    }
    return null
  }

  /** While muted (question audio playing) frames are ignored; any candidate is dropped. */
  setMuted(muted: boolean): void {
    this.muted = muted
    this.reset()
  }

  private reset(): void {
    this.onsetMs = null
    this.speechMs = 0
  }
}
