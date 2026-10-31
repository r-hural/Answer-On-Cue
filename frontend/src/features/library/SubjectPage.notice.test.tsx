import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { doc, setupWorld, version } from './library.mocks'
import { SubjectPage } from './SubjectPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/library/1']}>
      <Routes>
        <Route path="/library/:subjectId" element={<SubjectPage />} />
        <Route path="*" element={<p>Elsewhere</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const latest = version({ id: 20, version: 2, created_at: '2026-10-20T10:00:00' })

function world(sourceReviewCount?: number) {
  setupWorld({ documents: [doc({ id: 1, title: 'Ateny', latest_version: latest, version_count: 2 })] })
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
}

describe('SubjectPage: Replace and the review notice', () => {
  it('has a Replace link on the document row and keeps the detail button unique', async () => {
    world()
    renderPage()
    const row = await screen.findByRole('link', { name: 'Replace Ateny with a new version' })
    expect(row).toHaveAttribute('href', '/library/1/documents/1/new-version')
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    await screen.findByText('Version 2 of 2 · uploaded 20 Oct')
    const detail = screen.getAllByRole('link', { name: 'Replace with a new version' })
    expect(detail).toHaveLength(1)
    expect(detail[0]).toHaveAttribute('href', '/library/1/documents/1/new-version')
  })

  it('shows the needs-review notice inside the Questions section, after its head', async () => {
    world(3)
    renderPage()
    const link = await screen.findByRole('link', { name: '3 questions need review' })
    expect(link).toHaveAttribute('href', '/library/1/questions?filter=review')
    const notice = link.closest('.notice') as HTMLElement
    expect(notice).toHaveClass('notice', 'warn')
    expect(notice).toHaveTextContent('3 questions need review after a document was replaced.')
    const questions = document.getElementById('questions') as HTMLElement
    expect(questions.contains(notice)).toBe(true)
    expect(notice.previousElementSibling).toHaveClass('section-head')
    expect(notice.parentElement).toBe(questions)
    expect(notice).not.toHaveClass('section')
  })

  it.each([0, undefined])('shows no notice for %s', async (count) => {
    world(count)
    renderPage()
    await screen.findByRole('button', { name: /Open Ateny/ })
    expect(screen.queryByText(/need review after a document was replaced/)).toBeNull()
  })
})
