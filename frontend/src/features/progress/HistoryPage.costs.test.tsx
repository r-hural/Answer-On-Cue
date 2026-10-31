import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { historyRow, setupReportsWorld } from '../rounds/reports.mocks'
import { HistoryPage } from './HistoryPage'

function renderPage() {
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([{ id: 1, name: 'Demokracja ateńska', description: null, document_count: 2 }]),
    ),
  )
  return render(
    <MemoryRouter initialEntries={['/progress/history']}>
      <Routes>
        <Route path="/progress/history" element={<HistoryPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('HistoryPage: the AI cost of each round', () => {
  it('shows <dt>AI cost</dt><dd>$0.48</dd> as in history.html, and nothing without a cost', async () => {
    setupReportsWorld({
      history: [historyRow({ round_id: 9, ai_cost_usd: 0.48 }), historyRow({ round_id: 8, ai_cost_usd: null })],
    })
    renderPage()
    const terms = await screen.findAllByText('AI cost')
    expect(terms).toHaveLength(1)
    expect(terms[0].tagName).toBe('DT')
    const value = terms[0].nextElementSibling as HTMLElement
    expect(value.tagName).toBe('DD')
    expect(value).toHaveTextContent('$0.48')
    const rows = screen.getAllByRole('listitem').filter((item) => item.querySelector('dl'))
    expect(rows).toHaveLength(2)
    expect(within(rows[1]).queryByText('AI cost')).toBeNull()
  })
})
