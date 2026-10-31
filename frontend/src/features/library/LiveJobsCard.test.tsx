import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { Job } from './api'
import { job } from './library.mocks'
import { hidesTaskCard, LiveJobsCard } from './LiveJobsCard'
import { intervals } from './strings'
import { useLiveJobs } from './useLiveJobs'

const original = { ...intervals }
beforeEach(() => {
  intervals.jobs = 20
  intervals.endedTask = 150
})
afterEach(() => {
  vi.useRealTimers()
  Object.assign(intervals, original)
})

/** GET /api/jobs?live=true answers from `live`; GET /api/jobs/:id from `byId`. */
function serve(live: () => Job[], byId: Record<number, Job> = {}) {
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

function renderCard(path = '/library') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LiveJobsCard />
    </MemoryRouter>,
  )
}

describe('LiveJobsCard', () => {
  it('shows a live job with its file name, steps and meter, linking to the subject', async () => {
    const calls = serve(() => [
      job({ id: 7, subject_id: 3, steps_done: 4, steps_total: 9, document_title: 'Błony biologiczne' }),
      job({ id: 8, kind: 'questions.generate', status: 'queued', subject_id: 4, document_version_id: null, document_title: null, subject_name: 'Historia' }),
    ])
    renderCard()
    const card = await screen.findByRole('complementary', { name: 'Running tasks' })
    const [first, second] = within(card).getAllByRole('link')
    expect(first).toHaveAttribute('href', '/library/3')
    expect(within(first).getByText('Extracting topics')).toBeInTheDocument()
    expect(within(first).getByText('Błony biologiczne')).not.toHaveAttribute('lang')
    expect(within(first).getByText('4 of 9')).toBeInTheDocument()
    expect(within(first).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '4')
    expect(second).toHaveAttribute('href', '/library/4')
    expect(within(second).getByText('Historia')).toBeInTheDocument()
    expect(calls[0]).toBe('?live=true')
  })

  it('renders nothing when no task is live', async () => {
    const calls = serve(() => [job({ status: 'done' })])
    const { container } = renderCard()
    await waitFor(() => expect(calls.length).toBeGreaterThan(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('leaves a one-line done row for a finished task, then disappears', async () => {
    let live = [job({ id: 7 })]
    serve(() => live, { 7: job({ id: 7, status: 'done', steps_done: 4 }) })
    const { container } = renderCard()
    expect(await screen.findByText('1 of 4')).toBeInTheDocument()
    live = []
    expect(await screen.findByText('done')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).toBeNull()
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('leaves a failed row and a partly done row for tasks that ended that way', async () => {
    let live = [job({ id: 7 }), job({ id: 8, document_title: 'Mitochondria' })]
    serve(() => live, {
      7: job({ id: 7, status: 'failed' }),
      8: job({ id: 8, status: 'partial', document_title: 'Mitochondria' }),
    })
    renderCard()
    expect(await screen.findAllByRole('progressbar')).toHaveLength(2)
    live = []
    expect(await screen.findByText('failed')).toHaveClass('chip', 'error')
    expect(screen.getByText('partly done')).toHaveClass('chip', 'warn')
  })

  it('leaves no ended row for a task cut off by a restart', async () => {
    let live = [job({ id: 7 })]
    const calls = serve(() => live, { 7: job({ id: 7, status: 'running', resumable: true }) })
    const { container } = renderCard()
    expect(await screen.findByText('1 of 4')).toBeInTheDocument()
    live = []
    const seen = calls.length
    await waitFor(() => expect(calls.length).toBeGreaterThan(seen + 2))
    expect(container).toBeEmptyDOMElement()
  })

  it('does not bring back an old ended row after the card was off', async () => {
    let live = [job({ id: 7 })]
    serve(() => live, { 7: job({ id: 7, status: 'done' }) })
    const { result, rerender } = renderHook(({ enabled }) => useLiveJobs(enabled), { initialProps: { enabled: true } })
    await waitFor(() => expect(result.current.live).toHaveLength(1))
    live = []
    await waitFor(() => expect(result.current.ended).toHaveLength(1))
    vi.useFakeTimers({ toFake: ['Date'] })
    rerender({ enabled: false })
    vi.setSystemTime(Date.now() + intervals.endedTask + 1000)
    rerender({ enabled: true })
    expect(result.current.ended).toEqual([])
  })

  it('does not bring back an old ended row when the tab is shown again', async () => {
    let live = [job({ id: 7 })]
    serve(() => live, { 7: job({ id: 7, status: 'done' }) })
    renderCard()
    expect(await screen.findByText('1 of 4')).toBeInTheDocument()
    live = []
    expect(await screen.findByText('done')).toBeInTheDocument()
    vi.useFakeTimers({ toFake: ['Date'] })
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    try {
      vi.setSystemTime(Date.now() + intervals.endedTask + 1000)
    } finally {
      delete (document as unknown as Record<string, unknown>).visibilityState
    }
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(screen.queryByText('done')).toBeNull()
  })

  it('stays away from the round and recording screens, without polling', async () => {
    const calls = serve(() => [job()])
    const { container } = renderCard('/practice/5/microphone')
    await new Promise((resolve) => setTimeout(resolve, 80))
    expect(container).toBeEmptyDOMElement()
    expect(calls).toEqual([])
    expect(hidesTaskCard('/rounds/5/answers/9')).toBe(true)
    expect(hidesTaskCard('/microphone')).toBe(true)
    expect(hidesTaskCard('/library/3')).toBe(false)
  })

  it('does not poll while the tab is hidden', async () => {
    const calls = serve(() => [job()])
    const visibility = Object.getOwnPropertyDescriptor(Document.prototype, 'visibilityState')
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    try {
      renderCard()
      await new Promise((resolve) => setTimeout(resolve, 80))
      expect(calls).toEqual([])
    } finally {
      delete (document as unknown as Record<string, unknown>).visibilityState
      if (visibility) expect(document.visibilityState).toBe('visible')
    }
  })
})
