import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { ActionBody, FollowUpState } from './api'
import { ExamPage } from './ExamPage'
import { CAPTURE_EVENTS, fakeSession, renderExam } from './examTestKit'
import { dontKnowReport, goodReport, noSpeechReport, setupReportsWorld } from './reports.mocks'
import {
  FOLLOW_UP_TEXT,
  applyAction,
  attempt,
  config,
  readyResult,
  result,
  setupRoundWorld,
  type RoundWorld,
} from './rounds.mocks'
import { roundStrings } from './strings'
import { createUploadQueue } from './uploads'

beforeEach(() => {
  sessionStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

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
  // the full feedback of the answer comes from the reports endpoint
  const reports = setupReportsWorld({ attempts: [{ ...goodReport(), attempt_id: 10 }] })
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

describe('ExamPage: processing and feedback (practice)', () => {
  it('shows the processing panel, then the band and total, and Enter opens the next question', async () => {
    const { world, open } = setup({ results: [result({ stage: 'transcribing' }), readyResult()] })
    renderExam({ open })
    await answerAndFinish()

    expect(await screen.findByText('Your answer is saved')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question 1 of 3')
    const steps = within(screen.getByRole('list', { name: 'Processing steps' }))
    expect(steps.getByText('Saved on this computer').parentElement).toHaveTextContent('Done')
    expect(steps.getByText('Writing down what you said').parentElement).toHaveTextContent('In progress')
    expect(steps.getByText('Checking content, language and fluency').parentElement).toHaveTextContent('Waiting')

    expect(await screen.findByText('Good')).toHaveClass('band', 'good')
    expect(screen.getByText('Factual score 74 · correctness 80 · completeness 70')).toBeInTheDocument()
    expect(screen.getByText('75')).toBeInTheDocument()
    expect(document.querySelector('.transcript')).toHaveTextContent(/ostracyzm to było takie głosowanie/)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question 1 of 3: feedback')
    expect(screen.getByRole('heading', { name: 'What was correct' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next question/ })).toBeInTheDocument()

    await user().keyboard('{Enter}')
    await waitFor(() => expect(named(world, 'next')).toHaveLength(1))
    expect(named(world, 'next')[0]).toMatchObject({ from_phase: 'feedback', attempt_id: 10 })
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 2 of 3' })).toBeInTheDocument()
  })

  it('names the last action "See the round results"', async () => {
    const { open } = setup({
      results: [readyResult()],
      state: { phase: 'feedback', current_index: 2, current_attempt: attempt({ id: 12, outcome: 'answered' }) },
    })
    renderExam({ open })
    expect(await screen.findByRole('button', { name: /See the round results/ })).toBeInTheDocument()
  })

  it('a failed answer is never shown as a score: message, recording, no band', async () => {
    const { world, open } = setup({ results: [FAILED] })
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText(/This answer could not be processed\./)).toBeInTheDocument()
    expect(screen.getByText(/The transcription service did not respond\./)).toBeInTheDocument()
    expect(screen.getByText(/This is a technical problem, not a score\./)).toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(screen.queryByText(/Factual/)).not.toBeInTheDocument()
    expect(document.querySelector('audio')).toHaveAttribute('src', '/api/recordings/31/audio')
    expect(world.round.phase).toBe('feedback')
  })

  it('"Try again" posts retry and polls until the result arrives', async () => {
    const { world, open } = setup({ results: [FAILED] })
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)
    world.results = [result({ stage: 'transcribing' }), readyResult()]
    await user().click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(paths(world, '/retry')).toHaveLength(1))
    expect(await screen.findByText('Your answer is saved')).toBeInTheDocument()
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(screen.queryByText(/could not be processed/)).not.toBeInTheDocument()
  })

  it('stops polling and says so when the server refuses the result', async () => {
    const { world, open } = setup()
    server.use(http.get('/api/attempts/:id/result', () => HttpResponse.json({ detail: 'Attempt not found.' }, { status: 404 })))
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText(/Attempt not found/)).toBeInTheDocument()
    const calls = world.requests.length
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(world.requests.length).toBe(calls)
  })

  it('stops polling after five minutes of processing and asks for a reload', async () => {
    const { world, open } = setup({ results: [result({ stage: 'transcribing' })] })
    renderExam({ open, pollMs: 60_000 })
    await answerAndFinish()
    await screen.findByText('Your answer is saved')
    for (let i = 0; i < 6; i += 1) await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(await screen.findByText(/taking much longer than usual/)).toBeInTheDocument()
    const calls = world.resultCalls
    await act(() => vi.advanceTimersByTimeAsync(120_000))
    expect(world.resultCalls).toBe(calls)
  })

  it('"Continue and try later" moves on with next', async () => {
    const { world, open } = setup({ results: [FAILED] })
    renderExam({ open })
    await answerAndFinish()
    await screen.findByText(/This answer could not be processed\./)
    await user().click(screen.getByRole('button', { name: /Continue and try later/ }))
    await waitFor(() => expect(named(world, 'next')).toHaveLength(1))
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 2 of 3' })).toBeInTheDocument()
  })

  it('a failure that cannot be retried offers only to continue', async () => {
    const mic = result({
      status: 'failed',
      stage: 'not_assessed',
      recording: null,
      failure: { kind: 'microphone', message: 'No recording was made.', retryable: false },
    })
    const { open } = setup({
      results: [mic],
      state: { phase: 'feedback', current_attempt: attempt({ id: 10, outcome: 'technical_failure' }) },
    })
    renderExam({ open })
    expect(await screen.findByText(/No recording was made\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue/ })).toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
  })

  it('silence: says no speech was heard and shows no band', async () => {
    const silent = readyResult({
      transcript: null,
      assessment: { status: 'no_speech', band: null, scores: null },
    })
    const { open } = setup({ results: [silent] })
    setupReportsWorld({ attempts: [{ ...noSpeechReport(), attempt_id: 10 }] })
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText('No speech was heard in this recording. It is not scored.')).toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(document.querySelector('audio')).not.toBeNull()
  })

  it('I don’t know: the band comes with the note that the question was not answered', async () => {
    const none = readyResult({
      outcome: 'dont_know',
      recording: null,
      transcript: null,
      assessment: {
        status: 'no_answer',
        band: 'weak',
        scores: { correctness: 0, completeness: 0, factual: 0, language: null, total: 0 },
      },
    })
    const { open } = setup({
      results: [none],
      state: { phase: 'feedback', current_attempt: attempt({ id: 10, outcome: 'dont_know' }) },
    })
    setupReportsWorld({ attempts: [{ ...dontKnowReport(), attempt_id: 10 }] })
    renderExam({ open })
    expect(await screen.findByText('Weak')).toBeInTheDocument()
    expect(screen.getByText('You chose “I don\'t know” · factual score 0')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Model answer' })).toBeInTheDocument()
  })

  it('a zero-length recording sends mic_failure, never stop, and uploads nothing', async () => {
    const mic = fakeSession({
      stop: vi.fn(async () => ({
        wav: new Blob([]),
        durationMs: 0,
        events: { listeningStartAt: 1, speechDetectedAt: null, captureStartAt: 2, stoppedAt: 3, preRollMs: 0, autoStarted: false },
      })),
    })
    const { world, open } = setup({ results: [FAILED] }, mic)
    renderExam({ open })
    await answerAndFinish()
    await waitFor(() => expect(named(world, 'mic_failure')).toHaveLength(1))
    expect(named(world, 'stop')).toHaveLength(0)
    await act(() => vi.advanceTimersByTimeAsync(300))
    expect(paths(world, '/events')).toHaveLength(0)
    expect(paths(world, '/audio')).toHaveLength(0)
  })

  it('uploads the events and then the audio of an answer', async () => {
    const { world, open } = setup({ results: [result()] })
    renderExam({ open })
    await answerAndFinish()
    await waitFor(() => expect(paths(world, '/audio')).toHaveLength(1))
    const order = world.requests.map((r) => r.path)
    expect(order.indexOf('/api/attempts/10/events')).toBeLessThan(order.indexOf('/api/attempts/10/audio'))
    expect(world.events[0].body).toMatchObject({
      start_reason: 'button',
      interrupted: false,
      listening_available: true,
      pre_roll_ms: 800,
      capture_start_at: 4000,
      prep_end_at: 4000,
      stopped_at: 9000,
      playback_ms: 0,
    })
  })
})

describe('ExamPage: a refused stop', () => {
  it('counts down to the next try and "Try now" finishes at once', async () => {
    const { world, open } = setup({ results: [result({ stage: 'transcribing' })] })
    let refuse = true
    server.use(
      http.post('/api/rounds/:id/actions', async ({ request }) => {
        const body = (await request.json()) as ActionBody
        world.requests.push({ method: 'POST', path: '/api/rounds/5/actions', body })
        if (body.action === 'stop' && refuse) return HttpResponse.json({ detail: 'The database is busy.' }, { status: 500 })
        applyAction(world, body)
        return HttpResponse.json(world.round)
      }),
    )
    renderExam({ open })
    await answerAndFinish()

    expect(await screen.findByText("Couldn't save the answer. Trying again in 2 s.")).toBeInTheDocument()
    expect(named(world, 'stop')).toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(1100))
    expect(screen.getByText("Couldn't save the answer. Trying again in 1 s.")).toBeInTheDocument()

    refuse = false
    await user().click(screen.getByRole('button', { name: 'Try now' }))
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(2))
    expect(await screen.findByText('Your answer is saved')).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't save the answer/)).not.toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(named(world, 'stop')).toHaveLength(2)
  })
})

describe('ExamPage: exam feedback timing', () => {
  it('moves straight to the next question while the upload runs, and never polls a result', async () => {
    const { world, open } = setup({ timing: 'exam' })
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 2 of 3' })).toBeInTheDocument()
    await waitFor(() => expect(paths(world, '/audio')).toHaveLength(1))
    await act(() => vi.advanceTimersByTimeAsync(500))
    expect(world.resultCalls).toBe(0)
    expect(screen.queryByText('Your answer is saved')).not.toBeInTheDocument()
    expect(open).toHaveBeenCalledTimes(2)
  })
})

describe('ExamPage: pause', () => {
  it('pause in recording: pause, interrupted events, audio, the interrupted panel, then restart', async () => {
    const mic = fakeSession()
    const { world, open } = setup({}, mic)
    renderExam({ open })
    const u = user()
    await u.click(await screen.findByRole('button', { name: /Answer now/ }))
    await screen.findByText('Recording')
    await waitFor(() => expect(screen.getByRole('button', { name: /Pause round/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Pause round/ }))

    expect(await screen.findByRole('heading', { name: 'Round paused during question 1' })).toBeInTheDocument()
    expect(named(world, 'pause')).toHaveLength(1)
    await waitFor(() => expect(paths(world, '/audio')).toHaveLength(1))
    expect(world.events[0].body.interrupted).toBe(true)
    expect(mic.session.dispose).toHaveBeenCalled()
    expect(screen.getByText(/not scored as your answer/)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Paused for 0:0\d, pause 1 in this round/ })).toBeInTheDocument()

    await u.click(screen.getByRole('button', { name: /Restart question 1/ }))
    await waitFor(() => expect(named(world, 'resume')).toHaveLength(1))
    expect(await screen.findByText('Listening')).toBeInTheDocument()
    expect(open).toHaveBeenCalledTimes(2)
  })

  it('a pause pressed while the answer is finishing is ignored: one stop, no pause', async () => {
    let release: () => void = () => {}
    const mic = fakeSession({
      stop: vi.fn(
        () =>
          new Promise((resolve) => {
            release = () => resolve({ wav: new Blob([new Uint8Array(100)], { type: 'audio/wav' }), durationMs: 2000, events: CAPTURE_EVENTS })
          }),
      ),
    })
    const { world, open } = setup({ results: [readyResult()] }, mic)
    renderExam({ open })
    const u = user()
    await u.click(await screen.findByRole('button', { name: /Answer now/ }))
    await screen.findByText('Recording')
    await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Finish answer/ }))
    await u.click(screen.getByRole('button', { name: /Pause round/ }))
    await act(async () => release())
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(1))
    expect(named(world, 'pause')).toHaveLength(0)
    expect(mic.session.stop).toHaveBeenCalledTimes(1)
  })

  it('"End round now" abandons the round and opens the summary', async () => {
    const { world, open } = setup()
    renderExam({ open })
    const u = user()
    await u.click(await screen.findByRole('button', { name: /Pause round/ }))
    await u.click(await screen.findByRole('button', { name: 'End round now' }))
    await waitFor(() => expect(named(world, 'abandon')).toHaveLength(1))
    expect(await screen.findByText('Summary page')).toBeInTheDocument()
  })

  it('pause in feedback shows the paused panel with the readout, and P resumes', async () => {
    const { world, open } = setup({
      results: [readyResult()],
      state: { phase: 'feedback', current_attempt: attempt({ id: 10, outcome: 'answered' }) },
    })
    renderExam({ open })
    await screen.findByText('Good')
    await user().click(screen.getByRole('button', { name: /Pause round/ }))
    expect(await screen.findByRole('heading', { name: 'Round paused' })).toBeInTheDocument()
    expect(screen.getByText('Paused for')).toBeInTheDocument()
    expect(screen.getByText('Pause 1 in this round')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(screen.getByRole('img', { name: /Paused for 0:0[3-5], pause 1/ })).toBeInTheDocument()
    await user().keyboard('p')
    await waitFor(() => expect(named(world, 'resume')).toHaveLength(1))
    expect(await screen.findByText('Good')).toBeInTheDocument()
  })

  it('a direct visit in the middle of an answer pauses the round and shows the interrupted panel', async () => {
    const { world, mic, open } = setup({
      state: { phase: 'recording', current_attempt: attempt({ id: 10, outcome: 'pending' }) },
    })
    renderExam({ open })
    expect(await screen.findByRole('heading', { name: 'Continue your round?' })).toBeInTheDocument()
    expect(named(world, 'pause')).toEqual([{ action: 'pause', from_phase: 'recording', attempt_id: 10 }])
    expect(mic.session.startListening).not.toHaveBeenCalled()
    expect(open).not.toHaveBeenCalled()
  })
})

describe('ExamPage: leaving', () => {
  it('a finished round opens the summary page', async () => {
    const { open } = setup({ state: { status: 'finished', phase: 'summary' } })
    renderExam({ open })
    expect(await screen.findByText('Summary page')).toBeInTheDocument()
    expect(open).not.toHaveBeenCalled()
  })
})

// ---- follow-ups ----

const FOLLOW_UPS_ON = { enabled: true, prep_seconds: 15, answer_seconds: 60 }

function setupFollowUps(
  followUps: FollowUpState['status'][],
  options: Parameters<typeof setupRoundWorld>[0] = {},
  mic = fakeSession(),
) {
  const timing = options.timing ?? 'practice'
  const world = setupRoundWorld({
    results: [readyResult()],
    ...options,
    state: {
      config: config({ feedback_timing: timing, prep_seconds: 10, answer_seconds: 120, follow_ups: FOLLOW_UPS_ON }),
      ...options.state,
    },
  })
  world.followUps = followUps
  const open = vi.fn(async () => mic.session)
  const reports = setupReportsWorld({ attempts: [{ ...goodReport(), attempt_id: 10 }] })
  return { world, mic, open, reports }
}

const stageNames = () => within(screen.getByRole('list', { name: 'Stage' })).getAllByRole('listitem').map((li) => li.textContent)
const currentStage = () => screen.getByRole('list', { name: 'Stage' }).querySelector('[aria-current="step"]')?.textContent
const noScores = () => {
  expect(document.querySelector('.scores')).toBeNull()
  expect(document.querySelector('.band')).toBeNull()
}

describe('ExamPage: follow-ups', () => {
  it('waits while the follow-up is prepared, asks it with its own timers, then shows the main feedback', async () => {
    const { world, open } = setupFollowUps(['generating', 'generating', 'ready'])
    renderExam({ open })
    await screen.findByRole('region', { name: 'Question' })
    expect(stageNames()).toEqual(['Question', 'Preparation', 'Answer', 'Processing', 'Follow-up', 'Feedback'])
    await answerAndFinish()

    const steps = within(await screen.findByRole('list', { name: 'Processing steps' }))
    await waitFor(() => expect(steps.getByText('Preparing a follow-up question').parentElement).toHaveTextContent('In progress'))
    expect(steps.getByText('Preparing a follow-up question').nextElementSibling).toHaveClass('chip', 'accent')
    expect(screen.getByText('Next, the follow-up question opens by itself, then feedback on both answers.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue without follow-up' })).toBeInTheDocument()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(0)
    noScores()

    // the round is read again until the follow-up is ready
    const card = await screen.findByRole('region', { name: 'Follow-up question' })
    const text = within(card).getByText(FOLLOW_UP_TEXT)
    expect(text).toHaveClass('content-q')
    expect(text).toHaveAttribute('lang', 'pl-PL')
    expect(within(card).getByText(/Follow-up question/).closest('.q-meta')).toHaveTextContent('Follow-up question · Based on what you just said')
    expect(screen.getByText('Follow-up to question 1')).toBeInTheDocument()
    expect(currentStage()).toBe('Follow-up')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question 1 of 3')

    // preparation runs on the follow-up timers
    expect(await screen.findByText('Answer time after this: 1:00')).toBeInTheDocument()
    expect(screen.getByRole('timer', { name: /Preparation: .* left of 0:15/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Skip follow-up/ })).toBeInTheDocument()
    expect(screen.getByText(/Feedback on your main answer comes after this follow-up\./)).toBeInTheDocument()
    noScores()
    expect(named(world, 'question_ready').at(-1)).toMatchObject({ attempt_id: 11 })

    // its answer runs on 60 s, then the follow-up is processed and the main feedback opens
    await user().click(screen.getByRole('button', { name: /Answer now/ }))
    expect(await screen.findByRole('timer', { name: /Answer time: .* left of 1:00/ })).toBeInTheDocument()
    expect(currentStage()).toBe('Follow-up')
    await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
    await user().click(screen.getByRole('button', { name: /Finish answer/ }))
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(2))
    expect(named(world, 'stop')[1]).toMatchObject({ attempt_id: 11 })
    expect(await screen.findByText('Good')).toHaveClass('band', 'good')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question 1 of 3: feedback')
    expect(currentStage()).toBe('Feedback')
    expect(world.round.current_attempt?.id).toBe(10)
  })

  it('keys work in the follow-up: Space answers, N is "I don\'t know", S skips it', async () => {
    const { world, open } = setupFollowUps(['ready'])
    renderExam({ open })
    await answerAndFinish()
    await screen.findByRole('region', { name: 'Follow-up question' })
    await screen.findByText('Answer time after this: 1:00')
    await user().keyboard('s')
    await waitFor(() => expect(named(world, 'skip')).toHaveLength(1))
    expect(named(world, 'skip')[0]).toMatchObject({ attempt_id: 11, from_phase: 'preparation' })
    expect(await screen.findByText('Good')).toBeInTheDocument()
  })

  it('a follow-up that could not be prepared: "Try again" or go on without it', async () => {
    const { world, open } = setupFollowUps(['failed'])
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText('Couldn’t prepare a follow-up question.')).toBeInTheDocument()
    expect(screen.getByText(/The provider is not responding\./)).toBeInTheDocument()
    const steps = within(screen.getByRole('list', { name: 'Processing steps' }))
    expect(steps.getByText('Preparing a follow-up question').parentElement).toHaveTextContent('Not prepared')
    const primaries = document.querySelectorAll('.btn.primary')
    expect(primaries).toHaveLength(1)
    expect(primaries[0]).toHaveTextContent('Try again')
    expect(screen.getByRole('button', { name: 'Continue without follow-up' })).toBeInTheDocument()
    noScores()

    await user().click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(paths(world, '/follow-ups/7/retry')).toHaveLength(1))
    expect(await screen.findByRole('region', { name: 'Follow-up question' })).toBeInTheDocument()
  })

  it('"Continue without follow-up" sends no_follow_up and opens the main feedback', async () => {
    const { world, open } = setupFollowUps(['failed'])
    renderExam({ open })
    await answerAndFinish()
    await user().click(await screen.findByRole('button', { name: 'Continue without follow-up' }))
    await waitFor(() => expect(named(world, 'no_follow_up')).toHaveLength(1))
    expect(named(world, 'no_follow_up')[0]).toMatchObject({ from_phase: 'processing', attempt_id: 10 })
    expect(await screen.findByText('Good')).toBeInTheDocument()
  })

  it('stops reading the round after 3 minutes and keeps the way out', async () => {
    const { world, open } = setupFollowUps(['generating'])
    let offset = 0 // the page clock jumps; the 50 ms polls keep their real pace
    renderExam({ open, now: () => performance.now() + offset })
    await answerAndFinish()
    await screen.findByText('Preparing a follow-up question')
    await waitFor(() => expect(world.roundReads).toBeGreaterThan(2))
    offset = 181_000
    expect(await screen.findByText(/taking much longer than usual/)).toBeInTheDocument()
    const reads = world.roundReads
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(world.roundReads).toBe(reads)
    expect(screen.getByRole('button', { name: 'Continue without follow-up' })).toBeInTheDocument()
  })

  it('exam: the follow-up is asked mid-round, then the next question; nothing is scored on screen', async () => {
    const { world, open } = setupFollowUps(['ready'], { timing: 'exam' })
    renderExam({ open })
    await answerAndFinish()
    await screen.findByRole('region', { name: 'Follow-up question' })
    expect(screen.getByText(/recording starts by itself when you speak\.$/)).toBeInTheDocument()
    noScores()
    await screen.findByText('Answer time after this: 1:00')
    await answerAndFinish()
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 2 of 3' })).toBeInTheDocument()
    expect(world.round.part).toBe('main')
    noScores()
  })

  it('exam: a failed main answer in processing shows the failure and "Try again"', async () => {
    const { open } = setupFollowUps([], {
      timing: 'exam',
      state: { phase: 'processing', current_attempt: attempt({ outcome: 'answered', processing_status: 'failed' }) },
    })
    server.use(http.get('/api/attempts/:id/result', () => HttpResponse.json(FAILED)))
    renderExam({ open })
    expect(await screen.findByText(/This answer could not be processed\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('primary')
    noScores()
  })

  it('without follow-ups the stages stay five', async () => {
    const { open } = setup()
    renderExam({ open })
    await screen.findByRole('region', { name: 'Question' })
    expect(stageNames()).toEqual(['Question', 'Preparation', 'Answer', 'Processing', 'Feedback'])
  })
})

// ---- answer again ----

const BRIEF = {
  attempt_id: 10,
  outcome: 'answered',
  status: 'ready' as const,
  band: 'needs_improvement' as const,
  factual: 51,
  total: 54,
  preparation_used_ms: 8000,
  long_pause_count: 3,
  fluency: 62,
}

function setupAgain(options: Parameters<typeof setupRoundWorld>[0] = {}, followUps = false, mic = fakeSession()) {
  const world = setupRoundWorld({
    results: [readyResult()],
    ...options,
    state: {
      config: config({
        prep_seconds: 10,
        answer_seconds: 120,
        immediate_repeat: true,
        follow_ups: { enabled: followUps, prep_seconds: 15, answer_seconds: 60 },
      }),
      ...options.state,
    },
  })
  world.offerAgain = true
  const open = vi.fn(async () => mic.session)
  const reports = setupReportsWorld({
    attempts: [
      { ...goodReport(), attempt_id: 10, again_attempt_id: null },
      { ...goodReport(), attempt_id: 11, attempt_kind: 'immediate', original: BRIEF },
    ],
  })
  return { world, open, reports }
}

const againBox = () => screen.queryByRole('heading', { level: 2, name: 'Answer again' })?.closest('section') ?? null

describe('ExamPage: answer again', () => {
  it('offers Answer again on practice feedback, then repeats the question with the main timers', async () => {
    const { world, open } = setupAgain()
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText('Good')).toHaveClass('band', 'good')
    const box = await waitFor(() => {
      const found = againBox()
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    expect(box).toHaveClass('box', 'tint')
    expect(
      within(box).getByText(
        'Same question, same time. The model answer is hidden while you answer. Your first score stays; the new attempt counts as practice with help.',
      ),
    ).toBeInTheDocument()
    const again = within(box).getByRole('button', { name: 'Answer again' })
    expect(again).toHaveClass('btn')
    expect(again).not.toHaveClass('primary')
    const primaries = document.querySelectorAll('.btn.primary')
    expect(primaries).toHaveLength(1)
    expect(primaries[0]).toHaveTextContent(/Next question/)

    await user().click(again)
    await waitFor(() => expect(named(world, 'answer_again')).toHaveLength(1))
    expect(named(world, 'answer_again')[0]).toMatchObject({ from_phase: 'feedback', attempt_id: 10 })
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 1 of 3: answer again' })).toBeInTheDocument()
    expect(await screen.findByText('Answer time after this: 2:00')).toBeInTheDocument()
    expect(screen.getByRole('timer', { name: /Preparation: .* left of 0:10/ })).toBeInTheDocument()
    expect(named(world, 'question_ready').at(-1)).toMatchObject({ attempt_id: 11 })

    await answerAndFinish()
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(2))
    expect(named(world, 'stop')[1]).toMatchObject({ attempt_id: 11 })
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 1 of 3: answered again' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'First answer and second answer' })).toBeInTheDocument()
    expect(againBox()).toBeNull()
    expect(world.round.can_answer_again).toBe(false)
  })

  it('after "I don\'t know" the box suggests saying the model answer in your own words', async () => {
    const { open } = setupAgain({ results: [readyResult({ outcome: 'dont_know' })] })
    renderExam({ open })
    const dontKnow = await screen.findByRole('button', { name: /^I don't know/ })
    await waitFor(() => expect(dontKnow).toBeEnabled())
    await user().click(dontKnow)
    const box = await waitFor(() => {
      const found = againBox()
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    expect(
      within(box).getByText(
        'Read the model answer, then try saying it in your own words. The model answer is hidden while you answer. It counts as practice with help, not as knowing it yourself.',
      ),
    ).toBeInTheDocument()
  })

  it('the repeat gets no follow-up panel, even with follow-ups on', async () => {
    const { world, open } = setupAgain(
      {
        results: [readyResult(), result({ stage: 'transcribing' }), readyResult()],
        state: {
          phase: 'feedback',
          current_attempt: attempt({ id: 10, outcome: 'answered', processing_status: 'ready' }),
          can_answer_again: true,
        },
      },
      true,
    )
    world.followUps = ['generating', 'ready']
    renderExam({ open })
    await user().click(await screen.findByRole('button', { name: 'Answer again' }))
    await answerAndFinish()
    expect(await screen.findByText('Your answer is saved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue without follow-up' })).toBeNull()
    expect(screen.queryByText('Preparing a follow-up question')).toBeNull()
    expect(await screen.findByRole('heading', { level: 1, name: 'Question 1 of 3: answered again' })).toBeInTheDocument()
    expect(world.round.part).toBe('main')
    expect(againBox()).toBeNull()
  })
})

// ---- recovery ----

describe('ExamPage: resume', () => {
  const PAUSED_AT = '2026-10-21T17:42:00Z'

  /** A round of 4 opened fresh while paused during question 2; question 1 is still being assessed. */
  function pausedWorld() {
    const ctx = setup()
    const r = ctx.world.round
    const items = [0, 1, 2, 3].map((i) => ({
      id: 50 + i,
      ordinal: i + 1,
      role: 'main' as const,
      question_version_id: 70 + i,
      status: i === 0 ? 'done' : i === 1 ? 'current' : 'pending',
      latest_attempt:
        i === 0
          ? attempt({ id: 10, outcome: 'answered', processing_status: 'processing' })
          : i === 1
            ? attempt({ id: 11, outcome: 'interrupted' })
            : null,
    }))
    Object.assign(r, {
      status: 'paused',
      phase: 'preparation',
      current_index: 1,
      total: 4,
      items,
      current: { item_id: 51, question_version_id: 71, text: 'Wyjaśnij, czym był ostracyzm w Atenach.', style: 'open', difficulty: 'recall' },
      current_attempt: attempt({ id: 11, outcome: 'interrupted' }),
      clock: { server_now: PAUSED_AT, phase_started_at: null, prep_deadline_at: null, answer_deadline_at: null, paused_at: PAUSED_AT },
    })
    ctx.world.pausedFrom = 'preparation'
    return ctx
  }

  it('a paused round opened fresh asks "Continue your round?" with what was saved', async () => {
    const { open } = pausedWorld()
    renderExam({ open })
    const panel = (await screen.findByRole('heading', { name: 'Continue your round?', level: 2 })).closest('section') as HTMLElement
    const when = new Date(PAUSED_AT).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
    await waitFor(() =>
      expect(panel.querySelector('.lead')).toHaveTextContent(
        `Demokracja ateńska · Standard · Practice feedback · stopped on ${when} while you were answering question 2.`,
      ),
    )
    const subject = panel.querySelector('.lead .content') as HTMLElement
    expect(subject).not.toHaveAttribute('lang')
    expect(subject).toHaveTextContent('Demokracja ateńska')
    expect(within(panel).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Question 1: answered and saved (question 1 is still being assessed).',
      'Question 2: interrupted. It starts again from the beginning.',
      'Questions 3–4: not asked yet.',
    ])
    expect(within(panel).getByRole('heading', { name: 'Question 2', level: 3 })).toBeInTheDocument()
    expect(within(panel).getByText(/czym był ostracyzm/)).toHaveAttribute('lang', 'pl-PL')
    const primaries = document.querySelectorAll('.btn.primary')
    expect(primaries).toHaveLength(1)
    expect(primaries[0]).toHaveTextContent('Check microphone and continue')
    expect(open).not.toHaveBeenCalled()
  })

  it('offers the microphone check, ending the round, or later', async () => {
    const { world, open } = pausedWorld()
    renderExam({ open })
    await screen.findByRole('heading', { name: 'Continue your round?' })
    expect(screen.getByRole('link', { name: 'Check microphone and continue' })).toHaveAttribute('href', '/practice/5/microphone?resume=1')
    expect(screen.getByRole('link', { name: 'Not now' })).toHaveAttribute('href', '/')
    await user().click(screen.getByRole('button', { name: 'End round and see results' }))
    await waitFor(() => expect(named(world, 'abandon')).toHaveLength(1))
    expect(await screen.findByText('Summary page')).toBeInTheDocument()
    expect(named(world, 'resume')).toHaveLength(0)
  })

  it('a pause made on this page keeps the round-paused panel', async () => {
    const { open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await user().click(screen.getByRole('button', { name: /Pause round/ }))
    expect(await screen.findByRole('heading', { name: 'Round paused during question 1' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Continue your round?' })).not.toBeInTheDocument()
  })

  it('?resume=1 resumes the paused round once and drops the query', async () => {
    const { world, open } = pausedWorld()
    function Location() {
      const location = useLocation()
      return <p data-testid="location">{location.pathname + location.search}</p>
    }
    render(
      <MemoryRouter initialEntries={['/rounds/5?resume=1']}>
        <Location />
        <Routes>
          <Route path="/rounds/:roundId" element={<ExamPage uploads={createUploadQueue()} pollMs={50} open={open} />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(named(world, 'resume')).toHaveLength(1))
    expect(named(world, 'resume')[0]).toMatchObject({ from_phase: 'preparation', attempt_id: 11 })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/rounds\/5$/))
    expect(await screen.findByText('Listening')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(500))
    expect(named(world, 'resume')).toHaveLength(1)
  })

  const CUT_OFF = result({
    status: 'failed',
    stage: 'analysing',
    failure: { kind: 'interrupted', message: 'Processing was cut off. Try again.', retryable: true },
  })

  it('an answer cut off by a restart is recovered once and then polled to its result', async () => {
    const { world, open } = setup({ results: [CUT_OFF, readyResult()] })
    world.recoverAction = 'resumed'
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(paths(world, '/recover')).toEqual([expect.objectContaining({ method: 'POST', path: '/api/attempts/10/recover' })])
  })

  it('a cut-off whose paid call may have run says so, and "Try again" still retries', async () => {
    const { world, open } = setup({ results: [CUT_OFF] })
    world.recoverAction = 'confirm'
    renderExam({ open })
    await answerAndFinish()
    expect(await screen.findByText(roundStrings.recovery.mayHaveCharged)).toBeInTheDocument()
    expect(paths(world, '/recover')).toHaveLength(1)
    world.results = [result({ stage: 'transcribing' }), readyResult()]
    await user().click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(paths(world, '/retry')).toHaveLength(1))
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(paths(world, '/recover')).toHaveLength(1)
  })

  it('a round opened fresh in processing asks for recovery once before polling', async () => {
    const { world, open } = setup({
      results: [readyResult()],
      state: { phase: 'processing', current_attempt: attempt({ id: 10, outcome: 'answered', processing_status: 'processing' }) },
    })
    let resultCallsAtRecover: number | null = null
    let recoverCalls = 0
    server.use(
      http.post('/api/attempts/:id/recover', ({ params }) => {
        recoverCalls += 1
        resultCallsAtRecover = world.resultCalls
        return HttpResponse.json({ attempt_id: Number(params.id), action: 'completed', operation_id: null })
      }),
    )
    renderExam({ open })
    expect(await screen.findByText('Good')).toBeInTheDocument()
    expect(resultCallsAtRecover).toBe(0)
    expect(recoverCalls).toBe(1)
  })
})
