import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { SummaryPage } from './SummaryPage'
import type { ItemRow, RoundReport } from './reports'
import { item, knowledge, provisionalRoundReport, roundReport, setupReportsWorld } from './reports.mocks'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/rounds/5/summary']}>
      <Routes>
        <Route path="/rounds/:roundId/summary" element={<SummaryPage pollMs={20} />} />
        <Route path="/rounds/:roundId/answers/:attemptId" element={<p>Answer page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

/** The provisional round of the mocks with its failed third row patched. */
function withFailedRow(patch: Partial<ItemRow> = {}, round: Partial<RoundReport> = {}): RoundReport {
  const base = provisionalRoundReport()
  return {
    ...base,
    items: base.items.map((row) => (row.attempt_id === 12 ? { ...row, ...patch } : row)),
    ...round,
  }
}

/** The same round after the third answer was given up. */
function afterGiveUp(): RoundReport {
  const base = provisionalRoundReport()
  return roundReport({
    provisional: false,
    knowledge: knowledge({
      ...base.knowledge,
      unresolved: 0,
      not_assessed: 1,
      provisional: false,
    }),
    items: base.items.map((row) =>
      row.attempt_id === 12 ? item({ ...row, outcome: 'given_up', status: 'ready', failure: null }) : row,
    ),
    restarted_ordinals: [],
  })
}

const rowOf = (text: RegExp) => screen.getByText(text).closest('li') as HTMLElement

function giveUpHandler(onGiveUp?: () => void) {
  const calls: number[] = []
  server.use(
    http.post('/api/attempts/:id/give-up', ({ params }) => {
      calls.push(Number(params.id))
      onGiveUp?.()
      return HttpResponse.json({ attempt_id: Number(params.id), outcome: 'given_up', status: 'ready' })
    }),
  )
  return calls
}

describe('SummaryPage: Give up this answer', () => {
  it('a failed row offers Try again and Give up; giving up reloads the round', async () => {
    const world = setupReportsWorld({ rounds: [withFailedRow()] })
    // The round changes when the give-up arrives, as on the server: the page polls a provisional
    // round, so a state swapped before the clicks could reload the row between them.
    const calls = giveUpHandler(() => {
      world.rounds = [afterGiveUp()]
      world.roundCalls = 0
    })
    renderPage()
    const row = await waitFor(() => rowOf(/procedura ostracyzmu/))
    expect(within(row).getByRole('button', { name: 'Try again' })).toHaveClass('btn', 'small', 'primary')
    const giveUp = within(row).getByRole('button', { name: 'Give up this answer' })
    expect(giveUp).toHaveClass('btn', 'small')
    expect(giveUp).not.toHaveClass('primary')
    expect(screen.getByText('Provisional results.')).toBeInTheDocument()

    await userEvent.click(giveUp)
    await userEvent.click(within(row).getByRole('button', { name: 'Give up' }))

    await waitFor(() => expect(calls).toEqual([12]))
    const reloaded = await waitFor(() => {
      const found = rowOf(/procedura ostracyzmu/)
      expect(within(found).getByText('Not assessed')).toHaveClass('chip')
      return found
    })
    expect(within(reloaded).getByText('You gave up this answer. It is not part of the score.')).toBeInTheDocument()
    expect(within(reloaded).getByRole('link', { name: 'Feedback' })).toHaveAttribute('href', '/rounds/5/answers/12')
    expect(within(reloaded).queryByRole('button')).toBeNull()
    expect(screen.getByText('1 answer not assessed (left out of the score)')).toBeInTheDocument()
    expect(screen.queryByText('Provisional results.')).toBeNull()
  })

  it('has no give-up button while a practice round is open', async () => {
    setupReportsWorld({ rounds: [withFailedRow({}, { status: 'active', finished_at: null })] })
    renderPage()
    const row = await waitFor(() => rowOf(/procedura ostracyzmu/))
    expect(within(row).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Give up this answer' })).toBeNull()
  })

  it.each([
    ['microphone', 'The microphone stopped during the answer, so nothing was recorded.'],
    ['audio_missing', 'The recording of this answer never reached the app, so it was not assessed.'],
  ])('a %s failure that cannot be retried can still be given up', async (kind, message) => {
    setupReportsWorld({ rounds: [withFailedRow({ failure: { kind, message, retryable: false } })] })
    renderPage()
    const row = await waitFor(() => rowOf(/procedura ostracyzmu/))
    expect(within(row).getByRole('button', { name: 'Give up this answer' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Try again' })).toBeNull()
  })
})
