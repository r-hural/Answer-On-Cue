import { useEffect, useRef, type ReactNode } from 'react'

const FOCUSABLE = 'textarea, input, select, button, a[href], [tabindex]:not([tabindex="-1"])'

/**
 * The scrim and modal dialog of the topic editor: focus moves in and stays in (Tab wraps),
 * Escape cancels, and focus goes back to where it was when the dialog closes.
 */
export function TopicDialog({
  titleId,
  wide,
  onCancel,
  children,
}: {
  titleId: string
  wide?: boolean
  onCancel: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cancel = useRef(onCancel)
  useEffect(() => {
    cancel.current = onCancel
  })

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('textarea, input, select, button')?.focus()
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        cancel.current()
        return
      }
      const dialog = ref.current
      if (event.key !== 'Tab' || !dialog) return
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (item) => !item.hasAttribute('disabled'),
      )
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      const inside = dialog.contains(document.activeElement)
      if (event.shiftKey && (document.activeElement === first || !inside)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !inside)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('keydown', keydown)
      before?.focus()
    }
  }, [])

  return (
    <div className="scrim">
      <div
        ref={ref}
        className={wide ? 'dialog wide' : 'dialog'}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        {children}
      </div>
    </div>
  )
}
