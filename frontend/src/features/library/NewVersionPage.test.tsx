import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { versionChange, versionsWorld } from './library.mocks'
import { NewVersionPage } from './NewVersionPage'

function Where() {
  const location = useLocation()
  return <p data-testid="where">{location.pathname + location.search}</p>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/library/1/documents/1/new-version']}>
      <Routes>
        <Route path="/library/:subjectId/documents/:documentId/new-version" element={<NewVersionPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function choose(name = 'Demokracja_poprawione.pdf') {
  const zone = await screen.findByRole('button', { name: /Drop a PDF, DOCX or TXT file/ })
  fireEvent.drop(zone, { dataTransfer: { files: [new File(['%PDF'], name, { type: 'application/pdf' })] } })
}

describe('NewVersionPage: layout', () => {
  it('follows document--new-version', async () => {
    versionsWorld()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Replace with a new version' })).toBeInTheDocument()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Library' })).toHaveAttribute('href', '/library')
    const subject = within(crumbs).getByRole('link', { name: 'Demokracja ateńska' })
    expect(subject).not.toHaveAttribute('lang')
    const file = within(crumbs).getByRole('link', { name: 'Demokracja_atenska.pdf' })
    expect(file).toHaveClass('file-name')
    expect(file).not.toHaveAttribute('lang')
    expect(crumbs).toHaveTextContent(/New version$/)
    const meta = document.querySelector('.page-head p.num')!
    expect(meta).toHaveTextContent('Demokracja_atenska.pdf · now version 2 · 142 pages · 812 passages')
    expect(meta.querySelector('.file-name')).not.toHaveAttribute('lang')
    expect(screen.getByRole('heading', { level: 2, name: '1Choose the new file' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'The old version stays stored. Answers you gave keep pointing to the version they were asked from.',
      ),
    ).toBeInTheDocument()
    expect(document.querySelector('.drop')).not.toBeNull()
    expect(screen.queryByRole('button', { name: /^Create version/ })).toBeNull()
  })
})

describe('NewVersionPage: preview', () => {
  it('posts the file and shows what changes', async () => {
    const world = versionsWorld()
    renderPage()
    await choose()
    expect(await screen.findByText('Passages unchanged')).toBeInTheDocument()
    const preview = world.requests.find((r) => r.path === '/api/documents/1/versions/preview')
    expect(preview?.method).toBe('POST')
    const file = document.querySelector('ul.rows.compact li')!
    expect(within(file as HTMLElement).getByText('Demokracja_poprawione.pdf')).toHaveClass('file-name')
    expect(within(file as HTMLElement).getByText('146 pages')).toBeInTheDocument()
    expect(within(file as HTMLElement).getByText('Read')).toHaveClass('chip', 'ok')
    const rows = [...document.querySelectorAll('table.table tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    )
    expect(rows).toEqual([
      ['Passages unchanged', '790'],
      ['Passages changed', '14'],
      ['Passages removed', '8'],
      ['Passages new', '27'],
      ['Your exclusions carried over', '41 of 41'],
      ['Questions that cite changed or removed passages', '5'],
    ])
    expect(screen.getByText('Questions that cite changed or removed passages').tagName).toBe('STRONG')
    expect(screen.getByText(/are marked/)).toHaveTextContent(
      'These 5 questions are marked Needs review and are not asked until you check them. Nothing is changed silently.',
    )
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('shows topics left without passages only when there are some, and no sentence for 0 questions', async () => {
    versionsWorld({ preview: versionChange({ topics_without_material: 2, questions_to_review: 0 }) })
    renderPage()
    await choose()
    expect(await screen.findByText('Topics left without passages')).toBeInTheDocument()
    expect(screen.queryByText(/are marked/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Create version 3' })).toHaveClass('btn', 'primary')
  })

  it('shows the extraction message of a file that could not be read', async () => {
    versionsWorld({ preview: versionChange({ status: 'failed', message: 'The file could not be read.' }) })
    renderPage()
    await choose()
    expect(await screen.findByText('The file could not be read.')).toBeInTheDocument()
    expect(screen.queryByText('Read', { selector: '.chip' })).toBeNull()
  })

  it.each(['failed', 'too_little_text'])('cannot create a version from a file that was not read (%s)', async (status) => {
    versionsWorld({ preview: versionChange({ status, message: 'The file could not be read.' }) })
    renderPage()
    await choose()
    const create = await screen.findByRole('button', { name: 'Create version 3 and review the 5 questions' })
    expect(create).toBeDisabled()
    expect(create).toHaveAccessibleDescription(
      'No version can be created from a file that was not read. Choose another file.',
    )
  })
})

describe('NewVersionPage: create and cancel', () => {
  it('creates the version and opens the questions to review', async () => {
    const world = versionsWorld()
    renderPage()
    await choose()
    await userEvent.click(await screen.findByRole('button', { name: 'Create version 3 and review the 5 questions' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1/questions?filter=review')
    const apply = world.requests.find((r) => r.path.endsWith('/apply'))
    expect(apply?.path).toBe(`/api/documents/1/versions/${'a'.repeat(32)}/apply`)
    expect(apply?.body).toEqual({ fingerprint: 'fp-version' })
  })

  it('goes back to the document when no question needs review', async () => {
    versionsWorld({ preview: versionChange({ questions_to_review: 0 }) })
    renderPage()
    await choose()
    await userEvent.click(await screen.findByRole('button', { name: 'Create version 3' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1?document=1')
  })

  it('refreshes the summary when it changed meanwhile', async () => {
    const world = versionsWorld({ changed: versionChange({ questions_to_review: 6, fingerprint: 'fp-2' }) })
    renderPage()
    await choose()
    await userEvent.click(await screen.findByRole('button', { name: 'Create version 3 and review the 5 questions' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The document or its questions changed since you looked. Check the new summary.',
    )
    const create = screen.getByRole('button', { name: 'Create version 3 and review the 6 questions' })
    await userEvent.click(create)
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1/questions?filter=review')
    expect(world.requests.filter((r) => r.path.endsWith('/apply')).map((r) => r.body)).toEqual([
      { fingerprint: 'fp-version' },
      { fingerprint: 'fp-2' },
    ])
  })

  it('cancel discards the staged file and goes back', async () => {
    const world = versionsWorld()
    renderPage()
    await choose()
    await screen.findByText('Passages unchanged')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1?document=1')
    expect(world.requests.find((r) => r.method === 'DELETE')?.path).toBe(
      `/api/documents/1/versions/${'a'.repeat(32)}`,
    )
  })

  it('leaving the page without Create discards the staged file', async () => {
    const world = versionsWorld()
    renderPage()
    await choose()
    await screen.findByText('Passages unchanged')
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: 'Library' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library')
    await waitFor(() =>
      expect(world.requests.filter((r) => r.method === 'DELETE').map((r) => r.path)).toEqual([
        `/api/documents/1/versions/${'a'.repeat(32)}`,
      ]),
    )
  })

  it('creating does not discard the new version on the way out', async () => {
    const world = versionsWorld()
    renderPage()
    await choose()
    await userEvent.click(await screen.findByRole('button', { name: 'Create version 3 and review the 5 questions' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1/questions?filter=review')
    expect(world.requests.filter((r) => r.method === 'DELETE')).toEqual([])
  })

  it('cancel before choosing a file only goes back', async () => {
    const world = versionsWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/library/1?document=1')
    expect(world.requests).toEqual([])
  })
})

describe('NewVersionPage: errors', () => {
  it.each([
    [409, 'This file is identical to the latest version of the document.'],
    [413, 'The file is too large (limit 25 MB).'],
    [415, 'Supported formats: PDF, DOCX, TXT, MD.'],
    [409, 'Wait for the topic or question work of this subject to finish first.'],
  ])('shows the server message of a %i', async (status, detail) => {
    versionsWorld({ preview: { status, detail } })
    renderPage()
    await choose()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveClass('notice', 'error')
    expect(alert).toHaveTextContent(detail)
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Create version/ })).toBeNull())
  })
})
