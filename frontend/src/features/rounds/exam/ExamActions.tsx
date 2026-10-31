import type { ReactNode } from 'react'

export function Key({ children }: { children: string }) {
  return <kbd>{children}</kbd>
}

export interface ExamButton {
  label: string
  /** Shown only on wide screens, after the label. */
  wide?: string
  key?: string
  onClick: () => void
  disabled?: boolean
  primary?: boolean
}

function ActionButton({ button }: { button: ExamButton }) {
  const { label, wide, key, onClick, disabled, primary } = button
  return (
    <button type="button" className={primary ? 'btn primary big' : 'btn'} onClick={onClick} disabled={disabled}>
      {wide ? (
        <span>
          {label}
          {!wide.startsWith(',') && ' '}
          <span className="wide-only">{wide.trimStart()}</span>
        </span>
      ) : (
        label
      )}
      {key && (
        <>
          {' '}
          <Key>{key}</Key>
        </>
      )}
    </button>
  )
}

/** The pinned action row: the primary action first, the others after the spacer. */
export function ExamActions({ primary, others, children }: { primary?: ExamButton; others?: ExamButton[]; children?: ReactNode }) {
  return (
    <div className="actions">
      {primary && <ActionButton button={{ ...primary, primary: true }} />}
      <span className="grow" />
      {others?.map((button) => <ActionButton key={button.label} button={button} />)}
      {children}
    </div>
  )
}
