import type { Band } from '../api'
import { roundStrings } from '../strings'
import type { PauseMarker } from '../reports'

const t = roundStrings.report

/** Whole seconds ("8 s"); under one second one decimal ("0.3 s"). Never a bare zero for a missing value. */
export function seconds(ms: number): string {
  if (ms > 0 && ms < 1000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.round(ms / 1000)} s`
}

/** One decimal without a trailing ".0" ("0.8 s", "3.8 s", "0 s"). */
export function seconds1(ms: number): string {
  const value = Math.round(ms / 100) / 10
  return `${Number.isInteger(value) ? value : value.toFixed(1)} s`
}

/** Always one decimal ("4.1 s", "2.0 s"): the length of a pause. */
export function pauseSeconds(ms: number): string {
  return `${(Math.round(ms / 100) / 10).toFixed(1)} s`
}

/** m:ss of a position in the recording (whole seconds, not rounded up). */
export function position(ms: number): string {
  const total = Math.floor(Math.max(0, ms) / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** "unavailable (no speech)" for a missing measurement, else the formatted value. Never "0". */
export function metricOrUnavailable<T extends number>(
  value: T | null | undefined,
  reason?: string,
  format: (value: T) => string = String,
): string {
  if (value === null || value === undefined) {
    const why = reason ? (t.unavailableReasons[reason] ?? reason.replace(/_/g, ' ')) : null
    return why ? `${t.unavailable} (${why})` : t.unavailable
  }
  return format(value)
}

const BAND_CLASS: Record<Band, string> = {
  weak: 'band weak',
  needs_improvement: 'band needs',
  good: 'band good',
  very_good: 'band very-good',
}

const BAND_KEY: Record<Band, string> = {
  weak: 'weak',
  needs_improvement: 'needs',
  good: 'good',
  very_good: 'very-good',
}

export const BANDS: Band[] = ['weak', 'needs_improvement', 'good', 'very_good']

export function bandClass(band: Band): string {
  return BAND_CLASS[band]
}

/** The class of a band bar or count segment ("needs", "very-good"). */
export function bandKey(band: Band): string {
  return BAND_KEY[band]
}

export interface TimelineMark {
  /** Percent of the recording: where the pause starts and how wide it is. */
  left: number
  width: number
  /** Percent of the recording: where its mark button sits (the middle of the pause). */
  center: number
  startMs: number
  endMs: number
  /** Where a click on the mark seeks to: one second before the pause. */
  seekMs: number
  position: string
  length: string
}

const percent = (value: number): number => Math.max(0, Math.min(100, Math.round(value * 10) / 10))

/** Pause marks of the timeline; pauses are in recording time. */
export function timelineMarks(pauses: PauseMarker[], durationMs: number): TimelineMark[] {
  if (durationMs <= 0) return []
  return pauses.map((pause) => ({
    left: percent((pause.start_ms / durationMs) * 100),
    width: percent(((pause.end_ms - pause.start_ms) / durationMs) * 100),
    center: percent(((pause.start_ms + pause.end_ms) / 2 / durationMs) * 100),
    startMs: pause.start_ms,
    endMs: pause.end_ms,
    seekMs: Math.max(0, pause.start_ms - 1000),
    position: position(pause.start_ms),
    length: pauseSeconds(pause.end_ms - pause.start_ms),
  }))
}

/** "22 Oct, 18:05" in the viewer's time zone. */
export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
}

/** "22 Oct" in the viewer's time zone. */
export function dateOnly(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/** "18:05" in the viewer's time zone. */
export function timeOnly(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** Rounded score for display. */
export function score(value: number): string {
  return String(Math.round(value))
}
