import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { ActionBody } from './api'
import { fakeRead, fakeSession, renderExam } from './examTestKit'
import { QUESTIONS, applyAction, config, setupRoundWorld, type RoundWorld } from './rounds.mocks'

beforeEach(() => {
  sessionStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

const actions = (world: RoundWorld) =>
  world.requests.filter((r) => r.path === '/api/rounds/5/actions').map((r) => r.body as ActionBody)
const named = (world: RoundWorld, action: string) => actions(world).filter((body) => body.action === action)

function setup(options: Parameters<typeof setupRoundWorld>[0] = {}, mic = fakeSession()) {
  const world = setupRoundWorld({
    ...options,
    state: { config: config({ prep_seconds: 10, answer_seconds: 120 }), ...options.state },
  })
  const open = vi.fn(async () => mic.session)
  return { world, mic, open }
}

describe('ExamPage: question and preparation', () => {
  it('audio off: sends question_ready once and shows the preparation with the ring', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    expect(await screen.findByText('Listening')).toBeInTheDocument()
    expect(named(world, 'question_ready')).toEqual([
      { action: 'question_ready', from_phase: 'question', attempt_id: 10 },
    ])
    expect(screen.getByRole('timer')).toHaveAttribute('aria-label', 'Preparation: 0:10 left of 0:10')
    expect(screen.getByText('Answer time after this: 2:00')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question 1 of 3')
    await waitFor(() => expect(mic.session.startListening).toHaveBeenCalledTimes(1))
    expect(screen.getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
    expect(screen.queryByRole('button', { name: /Replay/ })).not.toBeInTheDocument()
  })

  it.each([
    [QUESTIONS[0], 'short'],
    [QUESTIONS[1], 'medium'],
    [QUESTIONS[2], 'long'],
  ])('sets data-size from the length of the question (%#)', async (text, size) => {
    const { open } = setup({ state: { current: { item_id: 50, question_version_id: 70, text, style: 'open', difficulty: 'recall' } } })
    renderExam({ open })
    const question = await screen.findByText(text.slice(0, 20), { exact: false })
    expect(question).toHaveAttribute('data-size', size)
    expect(question).toHaveAttribute('lang', 'pl-PL')
  })

  it('keeps one-letter Polish words with the next word', async () => {
    const text = 'Co robił Solon w Atenach i o czym pisał?'
    const { open } = setup({ state: { current: { item_id: 50, question_version_id: 70, text, style: 'open', difficulty: 'recall' } } })
    renderExam({ open })
    const question = await screen.findByText(/Co robił Solon/)
    expect(question.textContent).toBe('Co robił Solon w Atenach i o czym pisał?')
  })

  it('speech starts the answer by itself and the answer time runs from the onset', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await waitFor(() => expect(mic.session.startListening).toHaveBeenCalled())
    await mic.speak(performance.now() - 3000)
    await waitFor(() =>
      expect(named(world, 'start_answer')).toEqual([
        { action: 'start_answer', from_phase: 'preparation', attempt_id: 10, start_reason: 'auto' },
      ]),
    )
    expect(screen.getByText('Recording')).toBeInTheDocument()
    // 120 s from the onset, which was 3 s ago: not 2:00
    expect(screen.getByRole('timer').getAttribute('aria-label')).toMatch(/Answer time: 1:5[67] left of 2:00/)
    expect(mic.session.startRecording).not.toHaveBeenCalled()
  })

  it('the preparation deadline starts the recording', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    await waitFor(() => expect(named(world, 'start_answer')).toHaveLength(1))
    expect(named(world, 'start_answer')[0].start_reason).toBe('timer')
    expect(mic.session.startRecording).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Recording')).toBeInTheDocument()
  })

  it('preparation 0 starts the recording at once', async () => {
    const { world, mic, open } = setup({ state: { config: config({ prep_seconds: 0 }) } })
    renderExam({ open })
    expect(await screen.findByText('Recording')).toBeInTheDocument()
    expect(named(world, 'start_answer')[0].start_reason).toBe('timer')
    expect(mic.session.startRecording).toHaveBeenCalledTimes(1)
    expect(mic.session.startListening).not.toHaveBeenCalled()
  })

  it('a suspended tab shows the true remaining time when it wakes', async () => {
    let clock = 5000
    const { open } = setup()
    renderExam({ open, now: () => clock })
    await screen.findByText('Listening')
    expect(screen.getByRole('timer')).toHaveAttribute('aria-label', 'Preparation: 0:10 left of 0:10')
    clock += 6000 // no timer ran meanwhile
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('timer')).toHaveAttribute('aria-label', 'Preparation: 0:04 left of 0:10')
  })
})

describe('ExamPage: recording', () => {
  async function startRecording(options: Parameters<typeof setup>[0] = {}) {
    const ctx = setup(options)
    renderExam({ open: ctx.open })
    await screen.findByText('Listening')
    await waitFor(() => expect(ctx.mic.session.startListening).toHaveBeenCalled())
    await ctx.mic.speak(performance.now())
    await screen.findByText('Recording')
    await waitFor(() => expect(named(ctx.world, 'start_answer')).toHaveLength(1))
    return ctx
  }

  it('finishes by itself at 0:00 and shows low time in the last 20 seconds', async () => {
    const { world, mic } = await startRecording()
    await act(() => vi.advanceTimersByTimeAsync(99_000))
    expect(document.querySelector('.timer.low')).toBeNull()
    await act(() => vi.advanceTimersByTimeAsync(2_000))
    expect(document.querySelector('.timer.low')).not.toBeNull()
    expect(screen.getByText(/seconds left — finish your thought/)).toBeInTheDocument()
    expect(named(world, 'stop')).toHaveLength(0)
    await act(() => vi.advanceTimersByTimeAsync(19_500))
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(1))
    expect(mic.session.stop).toHaveBeenCalledTimes(1)
  })

  it('does not offer "Skip question" while recording', async () => {
    await startRecording()
    expect(screen.queryByRole('button', { name: /Skip/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Finish answer/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /I don't know/ })).toBeInTheDocument()
  })

  it('Space on the page answers now, then finishes the answer', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    const u = user()
    await u.keyboard(' ')
    await waitFor(() => expect(named(world, 'start_answer')).toHaveLength(1))
    expect(named(world, 'start_answer')[0].start_reason).toBe('button')
    expect(mic.session.startRecording).toHaveBeenCalledTimes(1)
    await screen.findByText('Recording')
    await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
    await u.keyboard(' ')
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(1))
  })

  it('Space on a focused button fires once, not twice', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    const button = await screen.findByRole('button', { name: /Answer now/ })
    const u = user()
    button.focus()
    await u.keyboard(' ')
    await waitFor(() => expect(named(world, 'start_answer')).toHaveLength(1))
    expect(mic.session.startRecording).toHaveBeenCalledTimes(1)
  })

  it('ignores keys in form controls and with a modifier', async () => {
    const { world, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    const u = user()
    await u.keyboard('s n ')
    await u.click(document.body)
    await u.keyboard('{Control>}s{/Control}{Alt>}n{/Alt}{Meta>} {/Meta}')
    await act(() => vi.advanceTimersByTimeAsync(300))
    expect(named(world, 'skip')).toHaveLength(0)
    expect(named(world, 'dont_know')).toHaveLength(0)
    expect(named(world, 'start_answer')).toHaveLength(0)
    input.remove()
  })

  it('every action carries the attempt id, and a second press while one is pending sends nothing', async () => {
    const { world, open } = setup()
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    renderExam({ open })
    await screen.findByText('Listening')
    server.use(
      http.post('/api/rounds/:id/actions', async ({ request }) => {
        const body = (await request.json()) as ActionBody
        world.requests.push({ method: 'POST', path: '/api/rounds/5/actions', body })
        await gate
        applyAction(world, body)
        return HttpResponse.json(world.round)
      }),
    )
    const u = user()
    await u.keyboard('s')
    await u.keyboard('s')
    await u.click(screen.getByRole('button', { name: /Skip/ }))
    await act(() => vi.advanceTimersByTimeAsync(200))
    expect(named(world, 'skip')).toHaveLength(1)
    release()
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(actions(world).every((body) => typeof body.attempt_id === 'number')).toBe(true)
  })

  it('"Pause round" is disabled while an action is in flight', async () => {
    const { world, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post('/api/rounds/:id/actions', async ({ request }) => {
        const body = (await request.json()) as ActionBody
        await gate
        applyAction(world, body)
        return HttpResponse.json(world.round)
      }),
    )
    const u = user()
    await u.click(screen.getByRole('button', { name: /Skip/ }))
    expect(screen.getByRole('button', { name: /Pause round/ })).toBeDisabled()
    release()
  })
})

describe('ExamPage: question audio and listening', () => {
  const premium = config({ prep_seconds: 10, question_audio: { mode: 'premium', provider: 'piper', voice: 'v', rate: 1 } })

  it('mutes the trigger while the question plays and on Replay, unmutes after', async () => {
    const { world, mic, open } = setup({ state: { config: premium } })
    const voice = fakeRead()
    renderExam({ open, read: voice.read })
    await screen.findByText('Reading aloud')
    await waitFor(() => expect(mic.session.setMuted).toHaveBeenCalledWith(true))
    expect(named(world, 'question_ready')).toHaveLength(0)
    expect(screen.getByRole('button', { name: /Read it myself/ })).toBeInTheDocument()

    await act(async () => voice.readings[0].finish({ playbackMs: 4321, failed: false }))
    await waitFor(() => expect(named(world, 'question_ready')).toHaveLength(1))
    expect(mic.session.setMuted).toHaveBeenLastCalledWith(false)
    await screen.findByText('Listening')
    await waitFor(() => expect(mic.session.startListening).toHaveBeenCalled())

    mic.session.setMuted.mockClear()
    await user().click(screen.getByRole('button', { name: /Replay question/ }))
    expect(voice.read).toHaveBeenCalledTimes(2)
    expect(mic.session.setMuted).toHaveBeenLastCalledWith(true)
    await act(async () => voice.readings[1].finish({ playbackMs: 4000, failed: false }))
    expect(mic.session.setMuted).toHaveBeenLastCalledWith(false)
    // a replay is not a second question_ready
    expect(named(world, 'question_ready')).toHaveLength(1)
  })

  it('offers no Replay while recording', async () => {
    const { mic, open } = setup({ state: { config: premium } })
    const voice = fakeRead()
    renderExam({ open, read: voice.read })
    await screen.findByText('Reading aloud')
    await act(async () => voice.readings[0].finish({ playbackMs: 1000, failed: false }))
    await screen.findByText('Listening')
    await waitFor(() => expect(mic.session.startListening).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /Replay question/ })).toBeInTheDocument()
    await mic.speak(performance.now())
    await screen.findByText('Recording')
    expect(screen.queryByRole('button', { name: /Replay question/ })).not.toBeInTheDocument()
    await user().keyboard('r')
    expect(voice.read).toHaveBeenCalledTimes(1)
  })

  it('"Read it myself" (Enter) stops the voice and starts the preparation', async () => {
    const { world, open } = setup({ state: { config: premium } })
    const voice = fakeRead()
    renderExam({ open, read: voice.read })
    await screen.findByText('Reading aloud')
    await user().keyboard('{Enter}')
    expect(voice.readings[0].reading.stop).toHaveBeenCalled()
    await waitFor(() => expect(named(world, 'question_ready')).toHaveLength(1))
    expect(await screen.findByText('Listening')).toBeInTheDocument()
  })

  it('a voice error shows a notice and continues with the text', async () => {
    const { world, open } = setup({ state: { config: premium } })
    const voice = fakeRead()
    renderExam({ open, read: voice.read })
    await screen.findByText('Reading aloud')
    await act(async () => voice.readings[0].finish({ playbackMs: 0, failed: true }))
    expect(await screen.findByText('The question could not be read aloud.')).toBeInTheDocument()
    await waitFor(() => expect(named(world, 'question_ready')).toHaveLength(1))
    expect(await screen.findByText('Listening')).toBeInTheDocument()
  })

  it('an unavailable microphone session shows the timer-only variant; answering still works', async () => {
    const mic = fakeSession({ available: false, reason: 'No microphone found.' })
    const { world, open } = setup({}, mic)
    renderExam({ open })
    expect(await screen.findByText(/recording will not start when you speak/)).toBeInTheDocument()
    expect(screen.getByText('Not listening')).toBeInTheDocument()
    expect(mic.session.startListening).not.toHaveBeenCalled()
    const u = user()
    await u.click(screen.getByRole('button', { name: /Answer now/ }))
    await screen.findByText('Recording')
    await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Finish answer/ }))
    await waitFor(() => expect(world.events).toHaveLength(1))
    expect(world.events[0].body.listening_available).toBe(false)
    expect(world.events[0].body.start_reason).toBe('button')
  })
})

describe('ExamPage: the session is disposed on every exit', () => {
  it('on finish', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    const u = user()
    await u.click(screen.getByRole('button', { name: /Answer now/ }))
    await screen.findByText('Recording')
    await waitFor(() => expect(screen.getByRole('button', { name: /Finish answer/ })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /Finish answer/ }))
    await waitFor(() => expect(named(world, 'stop')).toHaveLength(1))
    expect(mic.session.dispose).toHaveBeenCalled()
  })

  it("on I don't know", async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await user().click(screen.getByRole('button', { name: /I don't know/ }))
    await waitFor(() => expect(named(world, 'dont_know')).toHaveLength(1))
    expect(mic.session.dispose).toHaveBeenCalled()
  })

  it('on skip', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await user().click(screen.getByRole('button', { name: /Skip/ }))
    await waitFor(() => expect(named(world, 'skip')).toHaveLength(1))
    expect(mic.session.dispose).toHaveBeenCalled()
  })

  it('on pause', async () => {
    const { world, mic, open } = setup()
    renderExam({ open })
    await screen.findByText('Listening')
    await user().click(screen.getByRole('button', { name: /Pause round/ }))
    await waitFor(() => expect(named(world, 'pause')).toHaveLength(1))
    expect(mic.session.dispose).toHaveBeenCalled()
  })

  it('on unmount', async () => {
    const { mic, open } = setup()
    const view = renderExam({ open })
    await screen.findByText('Listening')
    view.unmount()
    expect(mic.session.dispose).toHaveBeenCalled()
  })

  it('opens a new session for each question', async () => {
    const mics = [fakeSession(), fakeSession()]
    const world = setupRoundWorld({ timing: 'exam', state: { config: config({ feedback_timing: 'exam', prep_seconds: 10 }) } })
    const open = vi.fn(async () => mics[open.mock.calls.length - 1].session)
    renderExam({ open })
    await screen.findByText('Listening')
    await user().click(screen.getByRole('button', { name: /I don't know/ }))
    await waitFor(() => expect(open).toHaveBeenCalledTimes(2))
    expect(within(screen.getByRole('heading', { level: 1 })).getByText('Question 2 of 3')).toBeInTheDocument()
    expect(world.round.current_index).toBe(1)
  })
})

describe('ExamPage: round type in the header', () => {
  it.each([
    ['standard', 'Standard'],
    ['adaptive', 'Adaptive'],
    ['extension', 'Adaptive extension'],
  ] as const)('a %s round shows "%s"', async (roundType, label) => {
    const { open } = setup({ state: { config: config({ prep_seconds: 10, answer_seconds: 120, round_type: roundType }) } })
    const { container } = renderExam({ open })
    await screen.findByText('Listening')
    const context = container.querySelector('.shell-ctx') as HTMLElement
    expect(context.textContent?.split(' · ')).toContain(label)
  })
})
