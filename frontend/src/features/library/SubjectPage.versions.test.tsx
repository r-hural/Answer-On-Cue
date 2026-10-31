import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { doc, setupWorld, version } from './library.mocks'
import { SubjectPage } from './SubjectPage'

function renderPage(entry = '/library/1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/library/:subjectId" element={<SubjectPage />} />
        <Route path="*" element={<p>Elsewhere</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const latest = version({ id: 20, version: 2, created_at: '2026-10-20T10:00:00' })

function replacedWorld(sourceReviewCount?: number) {
  const world = setupWorld({ documents: [doc({ id: 1, title: 'Ateny', latest_version: latest, version_count: 2 })] })
  server.use(
    http.get('/api/subjects/1', () =>
      HttpResponse.json({
        id: 1,
        name: 'Historia',
        description: null,
        document_count: 1,
        ...(sourceReviewCount === undefined ? {} : { source_review_count: sourceReviewCount }),
      }),
    ),
    http.get('/api/documents/1', () =>
      HttpResponse.json({
        ...doc({ id: 1, title: 'Ateny', latest_version: latest, version_count: 2 }),
        versions: [
          version({ id: 10, version: 1, created_at: '2026-10-19T10:00:00', answer_count: 12, round_count: 2 }),
          { ...latest, answer_count: 0, round_count: 0 },
        ],
      }),
    ),
  )
  return world
}

describe('SubjectPage: document versions', () => {
  it('shows the version line and the Replace entry on the document panel', async () => {
    replacedWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    expect(await screen.findByText('Version 2 of 2 · uploaded 20 Oct')).toBeInTheDocument()
    const replace = screen.getByRole('link', { name: 'Replace with a new version' })
    expect(replace).toHaveClass('btn')
    expect(replace).not.toHaveClass('primary')
    expect(replace).toHaveAttribute('href', '/library/1/documents/1/new-version')
  })

  it('lists the versions newest first, with the answers of older ones', async () => {
    replacedWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    const list = (await screen.findByRole('heading', { name: 'Versions' })).parentElement!
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((row) => row.querySelector('.title')?.textContent)).toEqual(['Version 2', 'Version 1'])
    expect(within(rows[0]).getByText('Current')).toHaveClass('chip')
    expect(rows[0]).toHaveTextContent('20 Oct')
    expect(rows[0]).not.toHaveTextContent('used by')
    expect(within(rows[1]).queryByText('Current')).toBeNull()
    expect(rows[1]).toHaveTextContent('19 Oct · used by 12 answers in 2 rounds')
  })

  it('opens the document named in ?document=', async () => {
    replacedWorld()
    renderPage('/library/1?document=1')
    expect(await screen.findByText('Version 2 of 2 · uploaded 20 Oct')).toBeInTheDocument()
  })

  it('shows the needs-review notice inside the Questions section', async () => {
    replacedWorld(3)
    renderPage()
    const link = await screen.findByRole('link', { name: '3 questions need review' })
    expect(link).toHaveAttribute('href', '/library/1/questions?filter=review')
    const notice = link.closest('.notice')!
    expect(notice).toHaveClass('notice', 'warn')
    expect(notice).toHaveTextContent('3 questions need review after a document was replaced.')
    expect(document.getElementById('questions')!.contains(notice)).toBe(true)
    expect(notice.previousElementSibling).toHaveClass('section-head')
  })

  it.each([0, undefined])('shows no notice for %s', async (count) => {
    replacedWorld(count)
    renderPage()
    await screen.findByRole('button', { name: /Open Ateny/ })
    expect(screen.queryByText(/need review after a document was replaced/)).toBeNull()
  })
})
