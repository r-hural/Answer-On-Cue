import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { AnswerPage } from './AnswerPage'
import type { AttemptReport } from './reports'
import { failedReport, followUpReport, givenUpReport, setupReportsWorld } from './reports.mocks'
import { createUploadQueue } from './uploads'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/rounds/5/answers/10']}>
      <Routes>
        <Route path="/rounds/:roundId/answers/:attemptId" element={<AnswerPage uploads={createUploadQueue()} pollMs={20} />} />
        <Route path="/rounds/:roundId/summary" element={<p>Summary page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const failed = (patch: Partial<AttemptReport> = {}): AttemptReport => ({ ...failedReport(), round_status: 'finished', ...patch })

/** The give-up endpoint; on success the attempt's next report is the given-up one. */
function giveUpHandler(world: ReturnType<typeof setupReportsWorld>, error: string | null = null) {
  const calls: number[] = []
  server.use(
    http.post('/api/attempts/:id/give-up', ({ params }) => {
      calls.push(Number(params.id))
      if (error) return HttpResponse.json({ detail: error }, { status: 409 })
      world.attempts[10] = givenUpReport()
      return HttpResponse.json(givenUpReport().result)
    }),
  )
  return calls
}

describe('AnswerPage: Give up this answer', () => {
  it('asks first; Cancel sends nothing', async () => {
    const world = setupReportsWorld({ attempts: [failed()] })
    const calls = giveUpHandler(world)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Give up this answer' }))
    expect(screen.getByText("It is left out of the score as not assessed, and it can't be tried again.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Give up' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('button', { name: 'Give up' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Give up this answer' })).toBeInTheDocument()
    expect(calls).toEqual([])
  })

  it('gives up, reloads and shows "Not assessed."', async () => {
    const world = setupReportsWorld({ attempts: [failed()] })
    const calls = giveUpHandler(world)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Give up this answer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Give up' }))
    expect(await screen.findByText('Not assessed.')).toBeInTheDocument()
    expect(calls).toEqual([10])
    expect(
      screen.getByText('You gave up this answer, so it is left out of the round score. It is never counted as Weak.'),
    ).toBeInTheDocument()
    expect(document.querySelector('audio')).not.toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(document.querySelector('.band')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Give up this answer' })).toBeNull()
  })

  it('shows a refusal in the action error notice', async () => {
    const world = setupReportsWorld({ attempts: [failed()] })
    giveUpHandler(world, 'This answer is still being processed.')
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Give up this answer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Give up' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This answer is still being processed.')
  })

  it.each(['active', 'paused'])('has no give-up button while the round is %s', async (status) => {
    setupReportsWorld({ attempts: [failed({ round_status: status })] })
    renderPage()
    await screen.findByRole('button', { name: 'Try again' })
    expect(screen.queryByRole('button', { name: 'Give up this answer' })).toBeNull()
  })

  it('offers it in an ended-early round too', async () => {
    setupReportsWorld({ attempts: [failed({ round_status: 'abandoned' })] })
    renderPage()
    expect(await screen.findByRole('button', { name: 'Give up this answer' })).toBeInTheDocument()
  })

  it('has no give-up button for a failed follow-up answer', async () => {
    const follow = followUpReport({ attempt_id: 10, round_status: 'finished', result: failedReport().result })
    setupReportsWorld({ attempts: [follow] })
    renderPage()
    await screen.findByRole('button', { name: 'Try again' })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Give up this answer' })).toBeNull())
  })
})
