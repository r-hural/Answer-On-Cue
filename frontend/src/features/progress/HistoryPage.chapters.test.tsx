import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { historyRow, setupReportsWorld } from '../rounds/reports.mocks'
import { HistoryPage } from './HistoryPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/progress/history']}>
      <Routes>
        <Route path="/progress/history" element={<HistoryPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function mockSubjects() {
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([
        { id: 1, name: 'Historia PL', description: null, document_count: 2, chapter_count: 2 },
        { id: 2, name: 'Biologia komórki', description: null, document_count: 1, chapter_count: 1 },
      ]),
    ),
    http.get('/api/subjects/1/chapters', () =>
      HttpResponse.json([
        { id: 1, subject_id: 1, name: null, is_default: true, ordinal: 0, document_count: 1, topic_count: 1, question_count: 2 },
        { id: 2, subject_id: 1, name: 'Rewolucja francuska', is_default: false, ordinal: 1, document_count: 1, topic_count: 1, question_count: 2 },
      ]),
    ),
  )
}

const ROWS = [
  historyRow({ round_id: 9, chapter_id: 2, chapter_name: 'Rewolucja francuska' }),
  historyRow({ round_id: 8, chapter_id: 1, chapter_name: null }),
  historyRow({ round_id: 7 }),
]

const metaOf = (roundId: number) =>
  document.querySelector(`a[href="/rounds/${roundId}/summary"]`)!.closest('li')!.querySelector('.meta') as HTMLElement

describe('HistoryPage with chapters', () => {
  it('shows a chapter chip on rows that have a chapter', async () => {
    setupReportsWorld({ history: ROWS })
    mockSubjects()
    renderPage()
    await screen.findAllByRole('link', { name: /\d/ })
    const named = within(metaOf(9)).getByText('Rewolucja francuska')
    expect(named).toHaveClass('chip')
    expect(named).not.toHaveAttribute('lang')
    const unnamed = within(metaOf(8)).getByText('Unnamed chapter')
    expect(unnamed).toHaveClass('chip')
    expect(unnamed).not.toHaveAttribute('lang')
    expect(within(metaOf(7)).queryByText(/chapter|Rewolucja/)).toBeNull()
  })

  it('offers a Chapter filter for a chaptered subject only, and sends chapter_id', async () => {
    const world = setupReportsWorld({ history: ROWS })
    mockSubjects()
    renderPage()
    const subject = await screen.findByLabelText('Subject')
    await screen.findByRole('option', { name: 'Historia PL' })
    expect(screen.queryByLabelText('Chapter')).toBeNull()
    await userEvent.selectOptions(subject, '1')
    const chapter = await screen.findByLabelText('Chapter')
    expect(within(chapter).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'All chapters',
      'Unnamed chapter',
      'Rewolucja francuska',
    ])
    await userEvent.selectOptions(chapter, '2')
    await waitFor(() => expect(world.historyQueries).toContain('?subject_id=1&chapter_id=2'))
    await userEvent.selectOptions(subject, '2')
    await waitFor(() => expect(world.historyQueries.at(-1)).toBe('?subject_id=2'))
    expect(screen.queryByLabelText('Chapter')).toBeNull()
    await userEvent.selectOptions(subject, '')
    await waitFor(() => expect(world.historyQueries.at(-1)).toBe(''))
    expect(screen.queryByLabelText('Chapter')).toBeNull()
  })
})
