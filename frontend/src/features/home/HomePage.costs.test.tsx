import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { summaryNoCeiling, summaryWithCeiling } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { HomePage } from './HomePage'

function backend() {
  server.use(
    http.get('/api/providers/status', () =>
      HttpResponse.json({
        keys: [{ provider: 'openai', configured: true, masked: '••••abcd', region: null }],
        roles: { generation: { provider: 'openai', model: 'm' } },
      }),
    ),
    http.get('/api/subjects', () =>
      HttpResponse.json([{ id: 1, name: 'Historia', description: null, document_count: 1 }]),
    ),
    http.get('/api/rounds', () => HttpResponse.json([])),
  )
}

function renderHome() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('HomePage: This month', () => {
  it('shows rounds, AI usage of the ceiling, the meter and the Usage link', async () => {
    backend()
    server.use(http.get('/api/costs/summary', () => HttpResponse.json(summaryWithCeiling)))
    renderHome()
    const heading = await screen.findByRole('heading', { name: 'This month' })
    const box = heading.closest('section') as HTMLElement
    expect(box).toHaveClass('box', 'tint')
    expect(within(box).getByText('6 rounds · AI usage about $2.40 of the $15.00 ceiling')).toHaveClass('num')
    expect(within(box).getByRole('img', { name: '16% of the monthly ceiling used' })).toHaveClass('meter-bar')
    expect(within(box).getByRole('link', { name: 'Usage' })).toHaveAttribute('href', '/settings/usage')
  })

  it('without a ceiling: no ceiling in the line and no meter', async () => {
    backend()
    server.use(http.get('/api/costs/summary', () => HttpResponse.json(summaryNoCeiling)))
    renderHome()
    const heading = await screen.findByRole('heading', { name: 'This month' })
    const box = heading.closest('section') as HTMLElement
    expect(within(box).getByText('6 rounds · AI usage about $2.40')).toBeInTheDocument()
    expect(within(box).queryByRole('img')).toBeNull()
  })
})
