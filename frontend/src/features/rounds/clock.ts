/** Timers follow deadlines (performance.now() values), never a count of ticks. */
export function remainingMs(deadline: number, now: number): number {
  return Math.max(0, deadline - now)
}

/** m:ss, partial seconds rounded up so "0:00" means the time is really over. */
export function formatClock(ms: number): string {
  const total = Math.ceil(Math.max(0, ms) / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

const LOW_TIME_MS = 20_000

/** The calm-amber state: the last 20 s, or the last quarter of a short answer time. */
export function isLow(remaining: number, total: number): boolean {
  return remaining <= Math.min(LOW_TIME_MS, total / 4)
}

export type QuestionSize = 'short' | 'medium' | 'long'

export function questionSize(text: string): QuestionSize {
  const length = text.length
  if (length <= 120) return 'short'
  if (length <= 240) return 'medium'
  return 'long'
}
