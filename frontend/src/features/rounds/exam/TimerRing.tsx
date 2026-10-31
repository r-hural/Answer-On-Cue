import type { CSSProperties, ReactNode } from 'react'

export function TimerRing({
  status,
  statusKind,
  fraction,
  clock,
  sub,
  ariaLabel,
  prep,
  low = false,
  note,
  role = 'timer',
}: {
  status: string
  statusKind: 'live' | 'listen'
  /** Remaining share of the time, 0..1. */
  fraction: number
  clock: string
  sub: string
  ariaLabel: string
  prep: boolean
  low?: boolean
  note: ReactNode
  role?: 'timer' | 'img'
}) {
  return (
    <div className={low ? 'timer low' : 'timer'} style={{ '--p': Math.max(0, Math.min(1, fraction)).toFixed(3) } as CSSProperties}>
      <span className={statusKind === 'listen' ? 'live listen' : 'live'} role="status">
        <i />
        {status}
      </span>
      <div className={prep ? 'ring prep' : 'ring'} role={role} aria-label={ariaLabel}>
        <div>
          <b>{clock}</b>
          <small>{sub}</small>
        </div>
      </div>
      {note}
    </div>
  )
}
