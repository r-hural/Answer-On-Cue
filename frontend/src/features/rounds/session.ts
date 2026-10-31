import { DEFAULT_NOISE_FLOOR_DB } from '../recording/capture'

/** The noise floor measured in the round's microphone check, kept for the exam screen. */
export const NOISE_FLOOR_KEY = 'aoc.noiseFloorDb'

export function writeNoiseFloor(floorDb: number | null): void {
  try {
    if (floorDb === null) sessionStorage.removeItem(NOISE_FLOOR_KEY)
    else sessionStorage.setItem(NOISE_FLOOR_KEY, String(floorDb))
  } catch {
    // storage unavailable: the exam uses the default floor
  }
}

export function readNoiseFloor(): number {
  try {
    const value = Number(sessionStorage.getItem(NOISE_FLOOR_KEY))
    if (sessionStorage.getItem(NOISE_FLOOR_KEY) !== null && Number.isFinite(value)) return value
  } catch {
    // fall through to the default
  }
  return DEFAULT_NOISE_FLOOR_DB
}
