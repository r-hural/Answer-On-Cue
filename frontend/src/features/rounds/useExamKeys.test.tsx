import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useExamKeys } from './useExamKeys'

afterEach(() => {
  document.body.innerHTML = ''
})

function press(target: Element | Document, key: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event
}

function focusable(html: string): HTMLElement {
  document.body.innerHTML = html
  return document.body.firstElementChild as HTMLElement
}

describe('useExamKeys', () => {
  it('handles Enter on the page body and prevents the default', () => {
    const enter = vi.fn()
    renderHook(() => useExamKeys({ enter }))
    const event = press(document.body, 'Enter')
    expect(enter).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
  })

  it.each([
    ['a link', '<a href="/x">Go</a>'],
    ['a disclosure summary', '<details><summary>More</summary></details>'],
    ['a role=button element', '<div role="button" tabindex="0">Do</div>'],
    ['a button', '<button>Do</button>'],
    ['a slider', '<div role="slider" tabindex="0"></div>'],
    ['a text area', '<textarea></textarea>'],
  ])('leaves Enter on %s to that element', (_name, html) => {
    const enter = vi.fn()
    renderHook(() => useExamKeys({ enter }))
    const element = focusable(html)
    const target = element.tagName === 'DETAILS' ? (element.firstElementChild as HTMLElement) : element
    const event = press(target, 'Enter')
    expect(enter).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })

  it('leaves Space on a link to the link too', () => {
    const space = vi.fn()
    renderHook(() => useExamKeys({ space }))
    press(focusable('<a href="/x">Go</a>'), ' ')
    expect(space).not.toHaveBeenCalled()
  })
})
