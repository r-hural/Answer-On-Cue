import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { CoveragePage } from './CoveragePage'
import { setupCoverageWorld } from './coverage.mocks'

function Where() {
  const { pathname, search } = useLocation()
  return <p data-testid="where">{pathname + search}</p>
}

function renderPage(entry = '/progress/coverage?subject=3') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <Routes>
        <Route path="/progress/coverage" element={<CoveragePage />} />
        <Route path="/practice" element={<p>Practice page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

function chaptered() {
  const subjects = [
    { id: 3, name: 'Demokracja ateńska', chapter_count: 2 },
    { id: 4, name: 'Biologia komórki', chapter_count: 1 },
  ]
  const world = setupCoverageWorld({ subjects })
  server.use(
    http.get('/api/subjects/3/chapters', ({ request }) => {
      world.requests.push(new URL(request.url).pathname)
      return HttpResponse.json([
        { id: 1, subject_id: 3, name: null, is_default: true, ordinal: 0, document_count: 1, topic_count: 1, question_count: 2 },
        { id: 2, subject_id: 3, name: 'Rewolucja francuska', is_default: false, ordinal: 1, document_count: 1, topic_count: 1, question_count: 2 },
      ])
    }),
  )
  return world
}

describe('CoveragePage with chapters', () => {
  it('offers All chapters and each chapter; choosing one filters the map', async () => {
    const world = chaptered()
    renderPage()
    const select = await screen.findByLabelText('Chapter')
    const options = within(select).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['All chapters', 'Unnamed chapter', 'Rewolucja francuska'])
    expect(options[1]).not.toHaveAttribute('lang')
    expect(options[2]).not.toHaveAttribute('lang')
    expect(select).toHaveValue('')
    await userEvent.selectOptions(select, '2')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/progress/coverage?subject=3&chapter=2'))
    await waitFor(() => expect(world.requests).toContain('/api/coverage/subjects/3?layer=all&chapter_id=2'))
  })

  it('opens on ?chapter= and keeps it in the practise action and the sources switch', async () => {
    const world = chaptered()
    renderPage('/progress/coverage?subject=3&chapter=2')
    await waitFor(() => expect(world.requests).toContain('/api/coverage/subjects/3?layer=all&chapter_id=2'))
    expect(world.requests).not.toContain('/api/coverage/subjects/3?layer=all')
    const action = await screen.findByRole('link', { name: 'Practise Needs work and Not practised' })
    expect(action).toHaveAttribute('href', '/practice?subject=3&type=adaptive&sources=all&chapter=2')
    expect(screen.getByRole('link', { name: 'My materials' })).toHaveAttribute(
      'href',
      '/progress/coverage?subject=3&layer=materials&chapter=2',
    )
  })
})

describe('CoveragePage with one chapter', () => {
  it('has no Chapter select and asks for no chapters', async () => {
    const world = setupCoverageWorld()
    renderPage()
    await screen.findByRole('link', { name: 'Practise Needs work and Not practised' })
    expect(screen.queryByLabelText('Chapter')).toBeNull()
    expect(world.requests.filter((r) => r.includes('chapter'))).toEqual([])
  })
})

describe('CoveragePage with a stale ?chapter=', () => {
  it('loads the whole map of a one-chapter subject', async () => {
    const world = setupCoverageWorld()
    renderPage('/progress/coverage?subject=3&chapter=9')
    await screen.findByRole('link', { name: 'Practise Needs work and Not practised' })
    expect(world.requests).toContain('/api/coverage/subjects/3?layer=all')
    expect(world.requests.filter((r) => r.includes('chapter'))).toEqual([])
  })

  it('loads the whole map when the chapters cannot be loaded', async () => {
    const world = chaptered()
    server.use(http.get('/api/subjects/3/chapters', () => HttpResponse.json({ detail: 'x' }, { status: 500 })))
    renderPage('/progress/coverage?subject=3&chapter=2')
    await screen.findByRole('link', { name: 'Practise Needs work and Not practised' })
    expect(world.requests).toContain('/api/coverage/subjects/3?layer=all')
    expect(screen.queryByLabelText('Chapter')).toBeNull()
  })
})
