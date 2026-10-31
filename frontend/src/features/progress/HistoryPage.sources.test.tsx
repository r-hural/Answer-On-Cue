import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { historyRow, setupReportsWorld } from '../rounds/reports.mocks'
import { HistoryPage } from './HistoryPage'

describe('HistoryPage: practice sources', () => {
  it('labels My materials and legacy expanded rounds, never All questions rounds', async () => {
    setupReportsWorld({
      history: [
        historyRow({ round_id: 3, source_policy: 'all' }),
        historyRow({ round_id: 2, source_policy: 'materials' }),
        historyRow({ round_id: 1, source_policy: 'expanded' }),
      ],
    })
    render(
      <MemoryRouter initialEntries={['/progress/history']}>
        <Routes>
          <Route path="/progress/history" element={<HistoryPage />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(3))
    const metas = [...document.querySelectorAll('.rows.rounds > li .meta > span:first-child')].map((n) => n.textContent)
    expect(metas).toEqual([
      'Standard · Practice',
      'Standard · Practice · My materials',
      'Standard · Practice · My materials + expand',
    ])
    expect(screen.getByRole('option', { name: 'All questions' })).toHaveValue('all')
  })
})
