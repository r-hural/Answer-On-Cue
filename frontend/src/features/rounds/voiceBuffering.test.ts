import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeSession } from './examTestKit'
import { ExamController, VOICE_HOLD_MS } from './examController'
import type { RoundState } from './api'
import { clearPreparedSpeech, prefetchRound, preparedSpeech, startReading, type Reading, type ReadingResult } from './questionAudio'
import { config, round, setupRoundWorld } from './rounds.mocks'
import { createUploadQueue } from './uploads'

const PREMIUM = { mode: 'premium' as const, provider: 'piper' as const, voice: 'pl_PL-bass-high', rate: 1 }

let controller: ExamController | null = null

afterEach(() => {
  controller?.dispose()
  controller = null
  clearPreparedSpeech()
  vi.useRealTimers()
})

describe('preparedSpeech: one request per reading', () => {
  it('shares a request still on its way', async () => {
    const request = vi.fn(async () => '/api/voice/cache/a.wav')
    const first = preparedSpeech('Pytanie?', PREMIUM, request)
    const second = preparedSpeech('Pytanie?', PREMIUM, request)
    expect(await first).toBe('/api/voice/cache/a.wav')
    expect(await second).toBe('/api/voice/cache/a.wav')
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('asks again after a failure, so the reading itself can report it', async () => {
    const request = vi.fn().mockRejectedValueOnce(new Error('ceiling')).mockResolvedValue('/api/voice/cache/b.wav')
    await expect(preparedSpeech('Pytanie?', PREMIUM, request)).rejects.toThrow('ceiling')
    expect(await preparedSpeech('Pytanie?', PREMIUM, request)).toBe('/api/voice/cache/b.wav')
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('keeps another voice or rate apart', async () => {
    const request = vi.fn(async () => '/x.wav')
    await preparedSpeech('Pytanie?', PREMIUM, request)
    await preparedSpeech('Pytanie?', { ...PREMIUM, rate: 1.2 }, request)
    expect(request).toHaveBeenCalledTimes(2)
  })
})

describe('prefetchRound', () => {
  const state = (patch: Partial<RoundState>): RoundState =>
    round({ config: config({ question_audio: PREMIUM, language: 'pl' }), upcoming: ['Następne?'], ...patch })

  it('prepares the current question while it is to be read, and the upcoming ones', () => {
    const request = vi.fn(async () => '/x.wav')
    const current = state({}).current?.text as string
    prefetchRound(state({}), request)
    expect(request.mock.calls.map((call) => (call as unknown[])[0])).toEqual([current, 'Następne?'])
  })

  it('prepares only the upcoming ones after the question phase', () => {
    const request = vi.fn(async () => '/x.wav')
    prefetchRound(state({ phase: 'feedback' }), request)
    expect(request.mock.calls.map((call) => (call as unknown[])[0])).toEqual(['Następne?'])
  })

  it('prepares nothing for the browser voice or with audio off', () => {
    const request = vi.fn(async () => '/x.wav')
    prefetchRound(round({ config: config({ question_audio: { mode: 'browser' } }), upcoming: ['A?'] }), request)
    prefetchRound(round({ upcoming: ['A?'] }), request)
    expect(request).not.toHaveBeenCalled()
  })
})

describe('startReading: started', () => {
  it('resolves when the audio begins, not when the voice is asked for', async () => {
    let answer: (url: string) => void = () => {}
    const speak = () => new Promise<string | null>((resolve) => (answer = resolve))
    let ended: () => void = () => {}
    const playUrl = vi.fn(() => ({ ended: new Promise<void>((resolve) => (ended = resolve)), stop() {} }))
    const reading = startReading('Pytanie?', PREMIUM, { speak, playUrl })
    let started = false
    void reading.started?.then(() => (started = true))
    await Promise.resolve()
    expect(started).toBe(false)
    answer('/a.wav')
    await vi.waitFor(() => expect(started).toBe(true))
    expect(playUrl).toHaveBeenCalled()
    ended()
    await reading.done
  })
})

describe('startReading: stop while the voice is made', () => {
  it('ends the reading at once, without waiting for the voice', async () => {
    const speak = () => new Promise<string | null>(() => {})
    const playUrl = vi.fn()
    const reading = startReading('Pytanie?', PREMIUM, { speak, playUrl })
    reading.stop()
    expect(await reading.done).toEqual({ playbackMs: 0, failed: false })
    await reading.started
    expect(playUrl).not.toHaveBeenCalled()
  })
})

describe('ExamController: the question waits for its voice', () => {
  /** A reading whose start and end the test decides. */
  function slowRead() {
    const readings: { reading: Reading; start: () => void; finish: (result: ReadingResult) => void }[] = []
    const read = vi.fn(() => {
      let start: () => void = () => {}
      let finish: (result: ReadingResult) => void = () => {}
      const started = new Promise<void>((resolve) => (start = resolve))
      const done = new Promise<ReadingResult>((resolve) => (finish = resolve))
      const reading: Reading = {
        done,
        started,
        stop: vi.fn(() => {
          start()
          finish({ playbackMs: 0, failed: false })
        }),
      }
      readings.push({ reading, start, finish })
      return reading
    })
    return { read, readings }
  }

  function start(read: ReturnType<typeof slowRead>['read'], prefetch = vi.fn()) {
    setupRoundWorld({ state: { config: config({ question_audio: PREMIUM }), upcoming: ['Następne?'] } })
    controller = new ExamController(5, {
      open: async () => fakeSession().session,
      now: () => performance.now(),
      read,
      prefetch,
      uploads: createUploadQueue(),
      pollMs: 50,
      noiseFloorDb: -60,
    })
    return controller
  }

  it('holds the text while the voice is made, then shows it as the reading starts', async () => {
    const { read, readings } = slowRead()
    const exam = start(read)
    await exam.load()
    await vi.waitFor(() => expect(readings).toHaveLength(1))
    expect(exam.getView()).toMatchObject({ questionShown: false, voicePreparing: true, reading: true })
    readings[0].start()
    await vi.waitFor(() => expect(exam.getView().questionShown).toBe(true))
    expect(exam.getView().voicePreparing).toBe(false)
  })

  it('shows the text without the voice after VOICE_HOLD_MS', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    const { read, readings } = slowRead()
    const exam = start(read)
    await exam.load()
    await vi.waitFor(() => expect(readings).toHaveLength(1))
    expect(exam.getView().questionShown).toBe(false)
    await vi.advanceTimersByTimeAsync(VOICE_HOLD_MS)
    expect(exam.getView()).toMatchObject({ questionShown: true, voicePreparing: true })
  })

  it('"Read it myself" shows the text at once', async () => {
    const { read, readings } = slowRead()
    const exam = start(read)
    await exam.load()
    await vi.waitFor(() => expect(readings).toHaveLength(1))
    exam.readMyself()
    await vi.waitFor(() => expect(exam.getView().questionShown).toBe(true))
  })

  it('asks for the coming questions to be prepared on every round read', async () => {
    const { read } = slowRead()
    const prefetch = vi.fn()
    const exam = start(read, prefetch)
    await exam.load()
    expect(prefetch).toHaveBeenCalledWith(expect.objectContaining({ upcoming: ['Następne?'] }))
  })
})

describe('ExamPage: the waiting question', () => {
  it('shows "Preparing the question…" with "Read it myself", then the question with "Preparing the voice"', async () => {
    const { screen } = await import('@testing-library/react')
    const { renderExam } = await import('./examTestKit')
    setupRoundWorld({ state: { config: config({ question_audio: PREMIUM }) } })
    let start: () => void = () => {}
    const read = vi.fn(
      (): Reading => ({
        done: new Promise<ReadingResult>(() => {}),
        started: new Promise<void>((resolve) => (start = resolve)),
        stop() {},
      }),
    )
    renderExam({ read, open: async () => fakeSession().session, prefetch: () => {} })
    expect(await screen.findByText('Preparing the question…')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Read it myself/ })).toBeTruthy()
    expect(screen.queryByText(round().current?.text as string)).toBeNull()
    start()
    expect(await screen.findByText(round().current?.text as string)).toBeTruthy()
    expect(screen.queryByText('Preparing the question…')).toBeNull()
  })
})
