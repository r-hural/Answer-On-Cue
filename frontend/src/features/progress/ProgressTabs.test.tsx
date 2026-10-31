import { render, screen, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ProgressTabs } from './ProgressTabs'

function tabs(ui: ReactElement) {
  render(<MemoryRouter>{ui}</MemoryRouter>)
  return within(screen.getByRole('navigation', { name: 'Progress views' }))
}

describe('ProgressTabs', () => {
  it('links Coverage and History and marks the current one', () => {
    const nav = tabs(<ProgressTabs current="coverage" />)
    expect(nav.getAllByRole('link').map((link) => link.textContent)).toEqual(['Coverage', 'History', 'Trends'])
    expect(nav.getByRole('link', { name: 'Coverage' })).toHaveAttribute('href', '/progress/coverage')
    expect(nav.getByRole('link', { name: 'Coverage' })).toHaveAttribute('aria-current', 'page')
    expect(nav.getByRole('link', { name: 'History' })).toHaveAttribute('href', '/progress/history')
    expect(nav.getByRole('link', { name: 'History' })).not.toHaveAttribute('aria-current')
  })

  it('keeps the chosen subject on the Coverage link', () => {
    const nav = tabs(<ProgressTabs current="history" subjectId={3} />)
    expect(nav.getByRole('link', { name: 'Coverage' })).toHaveAttribute('href', '/progress/coverage?subject=3')
    expect(nav.getByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page')
  })

  it('marks Trends and keeps the chosen subject on its link', () => {
    const nav = tabs(<ProgressTabs current="trends" subjectId={3} />)
    expect(nav.getByRole('link', { name: 'Trends' })).toHaveAttribute('aria-current', 'page')
    expect(nav.getByRole('link', { name: 'Trends' })).toHaveAttribute('href', '/progress/trends?subject=3')
    expect(nav.getByRole('link', { name: 'Coverage' })).toHaveAttribute('href', '/progress/coverage?subject=3')
    expect(nav.getByRole('link', { name: 'Coverage' })).not.toHaveAttribute('aria-current')
  })

  it('links Trends without a subject until one is chosen', () => {
    const nav = tabs(<ProgressTabs current="history" />)
    expect(nav.getByRole('link', { name: 'Trends' })).toHaveAttribute('href', '/progress/trends')
  })
})
