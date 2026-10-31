import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { ActionBody, FollowUpState } from './api'
import { fakeSession, renderExam } from './examTestKit'
import { givenUpReport, setupReportsWorld } from './reports.mocks'
import { attempt, config, givenUpResult, result, setupRoundWorld, syncAgain, type RoundWorld } from './rounds.mocks'
import { roundStrings } from './strings'

beforeEach(() => {
  sessionStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

const t = roundStrings.review
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
const actions = (world: RoundWorld) =>
  world.requests.filter((r) => r.path === '/api/rounds/5/actions').map((r) => r.body as ActionBody)
const named = (world: RoundWorld, action: string) => actions(world).filter((body) => body.action === action)
const paths = (world: RoundWorld, suffix: string) => world.requests.filter((r) => r.path.endsWith(suffix))

function setup(options: Parameters<typeof setupRoundWorld>[0] = {}, mic = fakeSession()) {
  const timing = options.timing ?? 'practice'
  const world = setupRoundWorld({
    ...options,
    state: { config: config({ feedback_timing: timing, prep_seconds: 10, answer_seconds: 120 }), ...options.state },
  })
  const open = vi.fn(async () => mic.session)
  // the not-assessed feedback of the given-up answer comes from the reports endpoint
  const reports = setupReportsWorld({ attempts: [givenUpReport({ attempt_id: 10, round_status: 'active' })] })
  return { world, mic, open, reports }
}

const FOLLOW_UPS_ON = { enabled: true, prep_seconds: 15, answer_seconds: 60 }

function setupFollowUps(
  followUps: FollowUpState['status'][],
  options: Parameters<typeof setupRoundWorld>[0] = {},
  mic = fakeSession(),
) {
  const timing = options.timing ?? 'practice'
  const world = setupRoundWorld({
    ...options,
    state: {
      config: config({ feedback_timing: timing, prep_seconds: 10, answer_seconds: 120, follow_ups: FOLLOW_UPS_ON }),
      ...options.state,
    },
  })
  world.followUps = followUps
  const open = vi.fn(async () => mic.session)
  const reports = setupReportsWorld({ attempts: [givenUpReport({ attempt_id: 10, round_status: 'active' })] })
  return { world, mic, open, reports }
}

/** Presses "Answer now" and then "Finish answer". */
async function answerAndFinish() {
  const u = user()
  await u.click(await screen.findByRole('button', { name: /Answer now/ }))
  await screen.findByText('Recording')
  await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
  await u.click(screen.getByRole('button', { name: /Finish answer/ }))
}

const FAILED = result({
  status: 'failed',
  stage: 'not_assessed',
  failure: { kind: 'timeout', message: 'The transcription service did not respond.', retryable: true },
})

const CUT_OFF = result({
  status: 'failed',
  stage: 'analysing',
  failure: { kind: 'interrupted', message: 'Processing was cut off. Try again.', retryable: true },
})

const PROCESSING_ATTEMPT = attempt({ id: 10, outcome: 'answered', processing_status: 'processing' })

/** GET /result answers `before` until the answer was given up, then the given-up result. */
function resultUntilGivenUp(world: RoundWorld, before: ReturnType<typeof result>) {
  server.use(
    http.get('/api/attempts/:id/result', ({ params }) => {
      world.resultCalls += 1
      const givenUp = paths(world, '/give-up').length > 0
      return HttpResponse.json(givenUp ? givenUpResult({ attempt_id: Number(params.id) }) : { ...before, attempt_id: Number(params.id) })
    }),
  )
}

async function confirmGiveUp() {
  const u = user()
  await u.click(await screen.findByRole('button', { name: t.giveUp }))
  await u.click(screen.getByRole('button', { name: t.giveUpYes }))
}

function expectNotAssessedFeedback() {
  expect(document.querySelector('.band')).toBeNull()
  expect(document.querySelector('.scores')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
}

describe('ExamPage: give up a failed answer during the round', () => {
  it('a practice failure offers "Give up this answer" next to "Continue and try later", with a confirm step', async () => {
    const { world, open } = setup({ results: [FAILED] })
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('primary')
    expect(screen.getByRole('button', { name: /Continue and try later/ })).toBeInTheDocument()

    const u = user()
    await u.click(screen.getByRole('button', { name: t.giveUp }))
    expect(screen.getByText(t.giveUpConfirm)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t.giveUpYes })).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: t.giveUpCancel }))

    expect(screen.getByRole('button', { name: t.giveUp })).toBeInTheDocument()
    expect(screen.queryByText(t.giveUpConfirm)).not.toBeInTheDocument()
    expect(paths(world, '/give-up')).toHaveLength(0)
  })

  it('giving up shows the not-assessed feedback, and Enter goes on with next', async () => {
    const { world, open } = setup({ results: [FAILED] })
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)
    const u = user()
    await u.click(screen.getByRole('button', { name: t.giveUp }))
    const readsBefore = world.roundReads
    await u.click(screen.getByRole('button', { name: t.giveUpYes }))

    expect(await screen.findByText(t.notAssessedTitle)).toBeInTheDocument()
    expect(screen.getByText(t.notAssessedText)).toBeInTheDocument()
    expect(paths(world, '/give-up')).toEqual([expect.objectContaining({ method: 'POST', path: '/api/attempts/10/give-up' })])
    expect(world.roundReads).toBeGreaterThan(readsBefore)
    expectNotAssessedFeedback()
    expect(screen.getByRole('button', { name: /Next question/ })).toBeInTheDocument()

    ;(document.activeElement as HTMLElement | null)?.blur()
    await u.keyboard('{Enter}')
    await waitFor(() => expect(named(world, 'next')).toHaveLength(1))
    expect(named(world, 'next')[0]).toMatchObject({ from_phase: 'feedback', attempt_id: 10 })
    expect(paths(world, '/retry')).toHaveLength(0)
    expect(paths(world, '/recover')).toHaveLength(0)
  })

  it('while giving up, the other ways on are disabled and Enter does nothing', async () => {
    const { world, open } = setup({ results: [FAILED] })
    server.use(
      http.post('/api/attempts/:id/give-up', async ({ params }) => {
        await delay(1000)
        return HttpResponse.json(givenUpResult({ attempt_id: Number(params.id) }))
      }),
    )
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)

    await confirmGiveUp()

    expect(await screen.findByRole('button', { name: t.givingUp })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Continue and try later/ })).toBeDisabled()
    ;(document.activeElement as HTMLElement | null)?.blur()
    await user().keyboard('{Enter}')
    expect(named(world, 'next')).toHaveLength(0)
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(await screen.findByText(t.notAssessedTitle)).toBeInTheDocument()
    expect(named(world, 'next')).toHaveLength(0)
  })

  it('a microphone failure (not retryable) can be given up too', async () => {
    const mic = result({
      status: 'failed',
      stage: 'not_assessed',
      recording: null,
      failure: { kind: 'microphone', message: 'No recording was made.', retryable: false },
    })
    const { world, open } = setup({
      results: [mic],
      state: { phase: 'feedback', current_attempt: attempt({ id: 10, outcome: 'technical_failure' }) },
    })
    renderExam({ open })
    expect(await screen.findByText(/No recording was made\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Continue/ })).toHaveClass('primary')

    await confirmGiveUp()

    expect(await screen.findByText(t.notAssessedTitle)).toBeInTheDocument()
    expectNotAssessedFeedback()
    expect(screen.getByRole('button', { name: /Next question/ })).toBeInTheDocument()
    expect(paths(world, '/give-up')).toHaveLength(1)
  })

  it('a refusal shows the server\'s reason, reads the result again and keeps the failure view', async () => {
    const { world, open } = setup({ results: [FAILED] })
    world.giveUpError = { status: 409, detail: 'This answer is still being processed.' }
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)
    const calls = world.resultCalls

    await confirmGiveUp()

    expect(await screen.findByText('This answer is still being processed.')).toBeInTheDocument()
    await waitFor(() => expect(world.resultCalls).toBeGreaterThan(calls))
    expect(screen.getByText(/This answer could not be processed\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
    expect(screen.getByRole('button', { name: t.giveUp })).toBeEnabled()
  })

  it('an exam with follow-ups, cut off by a restart, moves on to the next question', async () => {
    const { world, open } = setupFollowUps([], { timing: 'exam', state: { phase: 'processing', current_attempt: PROCESSING_ATTEMPT } })
    world.recoverAction = 'confirm'
    resultUntilGivenUp(world, CUT_OFF)
    renderExam({ open })
    expect(await screen.findByText(roundStrings.recovery.mayHaveCharged)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue and try later/ })).toBeInTheDocument()
    const recovers = paths(world, '/recover').length

    await confirmGiveUp()

    expect(await screen.findByRole('heading', { level: 1, name: 'Question 2 of 3' })).toBeInTheDocument()
    expect(paths(world, '/give-up')).toHaveLength(1)
    expect(paths(world, '/recover')).toHaveLength(recovers)
  })

  it('a lost move is repaired by recovery', async () => {
    const { world, open } = setup({ state: { phase: 'processing', current_attempt: PROCESSING_ATTEMPT } })
    world.giveUpSettles = false
    resultUntilGivenUp(world, CUT_OFF)
    server.use(
      http.post('/api/attempts/:id/recover', async ({ request, params }) => {
        world.requests.push({ method: request.method, path: new URL(request.url).pathname, body: undefined })
        const givenUp = world.round.current_attempt?.outcome === 'given_up'
        if (givenUp) {
          world.round.phase = 'feedback'
          syncAgain(world)
        }
        return HttpResponse.json({ attempt_id: Number(params.id), action: givenUp ? 'completed' : 'none', operation_id: null })
      }),
    )
    renderExam({ open })
    await screen.findByText(/Processing was cut off/)
    const recovers = paths(world, '/recover').length

    await confirmGiveUp()

    expect(await screen.findByText(t.notAssessedTitle)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next question/ })).toBeInTheDocument()
    expect(paths(world, '/recover')).toHaveLength(recovers + 1)
  })

  it('a follow-up answer is not offered for giving up', async () => {
    const { open } = setupFollowUps([], {
      state: {
        part: 'followup',
        phase: 'processing',
        current_attempt: attempt({ id: 12, attempt_kind: 'followup', outcome: 'answered', processing_status: 'processing' }),
      },
    })
    server.use(http.get('/api/attempts/:id/result', ({ params }) => HttpResponse.json({ ...FAILED, attempt_id: Number(params.id) })))
    renderExam({ open })
    expect(await screen.findByText(/This answer could not be processed\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue and try later/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t.giveUp })).not.toBeInTheDocument()
  })
})
