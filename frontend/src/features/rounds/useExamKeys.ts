import { useEffect, useRef } from 'react'

export type ExamKey = 'enter' | 'space' | 'n' | 's' | 'p' | 'r'
export type ExamKeyHandlers = Partial<Record<ExamKey, () => void>>

const KEYS: Record<string, ExamKey> = { Enter: 'enter', ' ': 'space', n: 'n', s: 's', p: 'p', r: 'r' }

/** Elements that act on Enter or Space themselves: a link, a disclosure, a custom button or slider. */
const OWNING_ROLES = ['button', 'slider']

/** A key that belongs to a form control or to a focused control is left to that element. */
function ownedByElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'A', 'SUMMARY', 'AUDIO'].includes(target.tagName)) return true
  return OWNING_ROLES.includes(target.getAttribute('role') ?? '')
}

/** Exam shortcuts: Enter, Space, N, S, P, R. The latest handlers are always used. */
export function useExamKeys(handlers: ExamKeyHandlers): void {
  const latest = useRef(handlers)
  useEffect(() => {
    latest.current = handlers
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.altKey || event.metaKey || event.repeat || event.defaultPrevented) return
      if (ownedByElement(event.target)) return
      const key = KEYS[event.key.length === 1 ? event.key.toLowerCase() : event.key]
      const handler = key ? latest.current[key] : undefined
      if (!handler) return
      event.preventDefault()
      handler()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}
