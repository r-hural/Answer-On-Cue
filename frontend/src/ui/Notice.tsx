import type { ReactNode } from 'react'

type Kind = 'info' | 'warn' | 'error' | 'ok'

const svg = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export const svgProps = svg

function Icon({ kind }: { kind: Kind }) {
  if (kind === 'info')
    return (
      <svg {...svg}>
        <circle cx="10" cy="10" r="7.5" />
        <path d="M10 6v4l2.5 2" />
      </svg>
    )
  if (kind === 'warn')
    return (
      <svg {...svg}>
        <path d="M10 3 2.5 16.5h15z" />
        <path d="M10 8.5v3.5" />
        <path d="M10 14.5v.01" />
      </svg>
    )
  if (kind === 'ok')
    return (
      <svg {...svg}>
        <path d="M4.5 10.5l3.5 3.5 7.5-8" />
      </svg>
    )
  return (
    <svg {...svg}>
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 6v4.5" />
      <path d="M10 13.5v.01" />
    </svg>
  )
}

/**
 * A notice in the template style: icon plus text on a wash. Errors are alerts, the rest status;
 * `label` turns it into a named region instead.
 */
export function Notice({
  kind,
  label,
  className,
  children,
}: {
  kind: Kind
  label?: string
  className?: string
  children: ReactNode
}) {
  const role = label ? 'region' : kind === 'error' ? 'alert' : 'status'
  return (
    <div className={`notice ${kind}${className ? ` ${className}` : ''}`} role={role} aria-label={label}>
      <Icon kind={kind} />
      <div>{children}</div>
    </div>
  )
}

export interface Note {
  text: string
  error: boolean
}

/** The outcome of a save or another action, under the section's buttons. */
export function NoteNotice({ note }: { note: Note | null }) {
  if (!note) return null
  return (
    <Notice kind={note.error ? 'error' : 'ok'} className="mt-4">
      {note.text}
    </Notice>
  )
}
