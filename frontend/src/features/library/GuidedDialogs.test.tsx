import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { Job } from './api'
import { job } from './library.mocks'
import { LiveJobsCard } from './LiveJobsCard'
import { intervals } from './strings'

const original = { ...intervals }
beforeEach(() => {
  intervals.jobs = 20
  intervals.endedTask = 150
  localStorage.clear()
})
afterEach(() => Object.assign(intervals, original))

function serve(live: () => Job[], byId: Record<number, Job>) {
  const calls: string[] = []
  server.use(
    http.get('/api/jobs', ({ request }) => {
      calls.push(new URL(request.url).search)
      return HttpResponse.json(live())
    }),
    http.get('/api/jobs/:id', ({ params }) => HttpResponse.json(byId[Number(params.id)])),
  )
  return calls
}

function Where() {
  const location = useLocation()
  return <p data-testid="where">{location.pathname + location.search}</p>
}

function renderCard(path = '/library') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
      <LiveJobsCard />
    </MemoryRouter>,
  )
}

const extraction = job({ id: 7, subject_id: 1, document_version_id: 30, document_title: 'Rewolucja', chapter_id: 2 })
const generation = job({
  id: 8,
  kind: 'questions.generate',
  subject_id: 1,
  document_version_id: null,
  document_title: null,
  subject_name: 'Historia',
  chapter_id: 2,
})

describe('Guided dialogs', () => {
  it('a finished extraction offers questions for that book; Generate questions opens the panel', async () => {
    let live = [extraction]
    serve(() => live, { 7: { ...extraction, status: 'done' } })
    renderCard()
    await screen.findByText('1 of 4')
    live = []
    const dialog = await screen.findByRole('dialog', { name: 'Topics ready for „Rewolucja”' })
    expect(within(dialog).getByText('Rewolucja')).not.toHaveAttribute('lang')
    expect(dialog.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(within(dialog).getByRole('button', { name: 'Generate questions' })).toHaveClass('primary')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Generate questions' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/library/1?chapter=2&generate=30')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('finished generation offers a round in the chapter, or more questions', async () => {
    let live = [generation]
    serve(() => live, { 8: { ...generation, status: 'partial' } })
    renderCard()
    await screen.findByText('1 of 4')
    live = []
    const dialog = await screen.findByRole('dialog', { name: 'Questions ready' })
    expect(within(dialog).getByRole('button', { name: 'Start a round' })).toHaveClass('primary')
    expect(dialog.querySelectorAll('.btn.primary')).toHaveLength(1)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Start a round' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/practice?subject=1&chapter=2&sources=all')
  })

  it('Generate more opens the Generate panel on all topics', async () => {
    let live = [generation]
    serve(() => live, { 8: { ...generation, status: 'done', chapter_id: null } })
    renderCard()
    await screen.findByText('1 of 4')
    live = []
    await userEvent.click(await screen.findByRole('button', { name: 'Generate more' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/library/1?generate=all')
  })

  it('opens once per job and browser; Not now closes it', async () => {
    let live = [extraction]
    serve(() => live, { 7: { ...extraction, status: 'done' } })
    const first = renderCard()
    await screen.findByText('1 of 4')
    live = []
    await userEvent.click(await screen.findByRole('button', { name: 'Not now' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    first.unmount()

    live = [extraction]
    renderCard()
    await screen.findByText('1 of 4')
    live = []
    expect(await screen.findByText('done')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('never opens for a failed job or one that ended before the page loaded', async () => {
    let live = [extraction]
    const calls = serve(() => live, { 7: { ...extraction, status: 'failed', error_message: 'x' } })
    renderCard()
    await screen.findByText('1 of 4')
    live = []
    expect(await screen.findByText('failed')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()

    // a job already done when the page loads never appears live, so it never opens a dialog
    live = []
    const before = calls.length
    await waitFor(() => expect(calls.length).toBeGreaterThan(before + 2))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('never opens on round, recording or microphone pages', async () => {
    const spy = vi.fn()
    server.use(
      http.get('/api/jobs', () => {
        spy()
        return HttpResponse.json([])
      }),
    )
    for (const path of ['/rounds/5', '/rounds/5/microphone', '/microphone']) {
      const view = renderCard(path)
      expect(screen.queryByRole('dialog')).toBeNull()
      view.unmount()
    }
    expect(spy).not.toHaveBeenCalled()
  })

  it('works without storage', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    try {
      let live = [extraction]
      serve(() => live, { 7: { ...extraction, status: 'done' } })
      renderCard()
      await screen.findByText('1 of 4')
      live = []
      expect(await screen.findByRole('dialog', { name: 'Topics ready for „Rewolucja”' })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })
})
