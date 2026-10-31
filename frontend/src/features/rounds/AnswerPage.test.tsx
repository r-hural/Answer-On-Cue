import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { server } from '../../test/msw'
import { AnswerPage } from './AnswerPage'
import { attemptReport, failedReport, followUpReport, goodReport, reportResult, setupReportsWorld } from './reports.mocks'
import { createUploadQueue, type UploadQueue } from './uploads'
import type { AttemptEventsIn } from './api'

function renderPage(uploads?: UploadQueue, pollMs = 20, path = '/rounds/5/answers/10') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rounds/:roundId/answers/:attemptId" element={<AnswerPage uploads={uploads ?? createUploadQueue()} pollMs={pollMs} />} />
        <Route path="/rounds/:roundId/summary" element={<p>Summary page</p>} />
        <Route path="/progress/history" element={<p>History page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.useRealTimers()
})

describe('AnswerPage', () => {
  it('shows the feedback under History › round › question, without exam keys', async () => {
    setupReportsWorld({ attempts: [goodReport()] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 3 of 10: feedback' })).toBeInTheDocument()
    const crumbs = document.querySelector('.crumbs') as HTMLElement
    await waitFor(() => expect(crumbs.textContent).toMatch(/^History › \d{1,2} \w{3}, \d{2}:\d{2} › Question 3$/))
    expect(within(crumbs).getByRole('link', { name: 'History' })).toHaveAttribute('href', '/progress/history')
    expect(crumbs.querySelectorAll('a')[1]).toHaveAttribute('href', '/rounds/5/summary')
    expect(screen.getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
    expect(screen.getByText('Good')).toHaveClass('band', 'good')
    expect(screen.getByRole('link', { name: 'Round summary' })).toHaveAttribute('href', '/rounds/5/summary')

    // the shell page has no exam shortcuts: Enter does not move on
    await userEvent.keyboard('{Enter}')
    expect(screen.queryByText('Summary page')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Next question/ })).not.toBeInTheDocument()
  })

  it('falls back to "Round" in the breadcrumb when the round report is missing', async () => {
    setupReportsWorld({ attempts: [goodReport()] })
    server.use(http.get('/api/reports/rounds/:id', () => HttpResponse.json({ detail: 'Round not found.' }, { status: 404 })))
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    expect((document.querySelector('.crumbs') as HTMLElement).textContent).toBe('History › Round › Question 3')
  })

  it("links to the attempt's own round, not the one in the address", async () => {
    setupReportsWorld({ attempts: [goodReport()] })
    const asked: string[] = []
    server.use(
      http.get('/api/reports/rounds/:id', ({ params }) => {
        asked.push(String(params.id))
        return HttpResponse.json({ detail: 'Round not found.' }, { status: 404 })
      }),
    )
    renderPage(undefined, 20, '/rounds/99/answers/10')
    await screen.findByRole('heading', { level: 1 })
    expect((document.querySelector('.crumbs') as HTMLElement).querySelectorAll('a')[1]).toHaveAttribute('href', '/rounds/5/summary')
    expect(screen.getByRole('link', { name: 'Round summary' })).toHaveAttribute('href', '/rounds/5/summary')
    await waitFor(() => expect(asked).toEqual(['5']))
  })

  it('says so when the answer does not exist', async () => {
    setupReportsWorld({ attempts: [] })
    renderPage()
    expect(await screen.findByText('Attempt not found.')).toBeInTheDocument()
  })

  it('shows only a notice while an exam round is still running', async () => {
    const withheld = attemptReport({
      feedback_timing: 'exam',
      round_status: 'active',
      result: reportResult({ withheld: true, transcript: null, metrics: null, assessment: null }),
      points: [],
      model_answer: null,
      model_answer_display: null,
      sources: [],
    })
    setupReportsWorld({ attempts: [withheld] })
    renderPage()
    expect(await screen.findByText(/Feedback appears when the round ends\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the round' })).toHaveAttribute('href', '/rounds/5')
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
  })

  it('reads the report again while the answer is processing', async () => {
    const processing = attemptReport({
      result: reportResult({ status: 'processing', stage: 'transcribing', transcript: null, metrics: null, assessment: null }),
      points: [],
      model_answer: null,
      model_answer_display: null,
      sources: [],
    })
    const world = setupReportsWorld({ attempts: [processing] })
    renderPage()
    expect(await screen.findByText('This answer is still being processed.')).toBeInTheDocument()
    world.attempts[10] = goodReport()
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(screen.queryByText('This answer is still being processed.')).not.toBeInTheDocument()
  })

  it('stops reading the report after five minutes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const processing = attemptReport({
      result: reportResult({ status: 'processing', stage: 'transcribing', transcript: null, metrics: null, assessment: null }),
      points: [],
    })
    let calls = 0
    setupReportsWorld({ attempts: [processing] })
    server.use(
      http.get('/api/reports/attempts/:id', () => {
        calls += 1
        return HttpResponse.json(processing)
      }),
    )
    renderPage(undefined, 60_000)
    await screen.findByText('This answer is still being processed.')
    for (let i = 0; i < 6; i += 1) await act(() => vi.advanceTimersByTimeAsync(60_000))
    const seen = calls
    await act(() => vi.advanceTimersByTimeAsync(180_000))
    expect(calls).toBe(seen)
    expect(seen).toBeLessThanOrEqual(7)
  })
})

describe('AnswerPage: an answer that could not be processed', () => {
  it('retries a retryable failure and shows the result when it arrives', async () => {
    const world = setupReportsWorld({ attempts: [failedReport('timeout', true)], retry: true })
    world.onRetry = () => {
      world.attempts[10] = goodReport()
    }
    renderPage()
    expect(await screen.findByText('Unresolved')).toHaveClass('chip', 'error')
    expect(document.querySelector('.band')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(world.retries).toEqual([10]))
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(screen.queryByText('Unresolved')).not.toBeInTheDocument()
  })

  it('without a queued recording an audio_missing failure offers nothing to press', async () => {
    setupReportsWorld({ attempts: [failedReport('audio_missing', false)] })
    renderPage()
    expect(await screen.findByText(/never reached the app/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Try again|Send the answer again/ })).not.toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
  })

  it('"Send the answer again" re-sends the queued recording and then retries', async () => {
    const world = setupReportsWorld({ attempts: [failedReport('audio_missing', false)], retry: true })
    world.onRetry = () => {
      world.attempts[10] = goodReport()
    }
    const postEvents = vi.fn().mockRejectedValue(new ApiError(400, 'Rejected.'))
    const uploadAnswer = vi.fn().mockResolvedValue({ attempt_id: 10, recording_id: 31, duration_ms: 1000 })
    const uploads = createUploadQueue({ postEvents, uploadAnswer, sleep: async () => undefined, backoffMs: [] })
    uploads.enqueue(10, {} as AttemptEventsIn, new Blob([new Uint8Array(10)]))
    await waitFor(() => expect(uploads.failed()).toHaveLength(1))

    renderPage(uploads)
    postEvents.mockResolvedValue({ attempt_id: 10, stored: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Send the answer again' }))
    await waitFor(() => expect(uploadAnswer).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(world.retries).toEqual([10]))
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(uploads.failed()).toHaveLength(0)
  })

  it('does not retry when the re-send fails again', async () => {
    const world = setupReportsWorld({ attempts: [failedReport('audio_missing', false)], retry: true })
    const postEvents = vi.fn().mockRejectedValue(new ApiError(400, 'Rejected.'))
    const uploads = createUploadQueue({ postEvents, uploadAnswer: vi.fn(), sleep: async () => undefined, backoffMs: [] })
    uploads.enqueue(10, {} as AttemptEventsIn, new Blob([new Uint8Array(10)]))
    await waitFor(() => expect(uploads.failed()).toHaveLength(1))
    renderPage(uploads)
    await userEvent.click(await screen.findByRole('button', { name: 'Send the answer again' }))
    expect(await screen.findByText(/could not be uploaded yet/)).toBeInTheDocument()
    expect(world.retries).toEqual([])
  })
})

describe('AnswerPage: a follow-up answer', () => {
  it('is the follow-up to its question, with its own scores and no follow-up box', async () => {
    setupReportsWorld({ attempts: [followUpReport({ ordinal: 3 })] })
    renderPage(undefined, 20, '/rounds/5/answers/21')
    expect(await screen.findByRole('heading', { level: 1, name: 'Follow-up to question 3' })).toBeInTheDocument()
    const crumbs = document.querySelector('.crumbs') as HTMLElement
    await waitFor(() => expect(crumbs.textContent).toMatch(/^History › .+ › Question 3 › Follow-up$/))
    expect(within(crumbs).getByRole('link', { name: 'Question 3' })).toHaveAttribute('href', '/rounds/5/answers/10')
    expect(document.querySelector('.scores')).not.toBeNull()
    expect(screen.getByText('Good')).toHaveClass('band', 'good')
    expect(screen.queryByRole('heading', { level: 2, name: 'Follow-up' })).toBeNull()
  })
})
