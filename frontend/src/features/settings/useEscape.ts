import { useEffect } from 'react'

/** Calls the handler when Escape is pressed anywhere, as long as the dialog is on screen. */
export function useEscape(onEscape: () => void) {
  useEffect(() => {
    const listen = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onEscape()
    }
    document.addEventListener('keydown', listen)
    return () => document.removeEventListener('keydown', listen)
  }, [onEscape])
}
