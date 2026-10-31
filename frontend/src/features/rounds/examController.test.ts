import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import { server } from '../../test/msw'
import { encodeWav } from '../recording/pcm'
import { CAPTURE_EVENTS, fakeRead, fakeSession } from './examTestKit'
import { bindWake, ExamController } from './examController'
import type { AttemptEventsIn, RoundState } from './api'
import {
  FOLLOW_UP_TEXT,
  attempt,
  config,
  followUpState,
  readyResult,
  round,
  setupRoundWorld,
} from './rounds.mocks'
import { createUploadQueue, type UploadQueue } from './uploads'

let controller: ExamController | null = null

afterEach(() => {
  controller?.dispose()
  controller = null
  vi.useRealTimers()
})

describe('ExamController: resync', () => {
  it('moves loadedAt to the clock of the re-read, so the paused readout continues from it', async () => {
    const world = setupRoundWorld()
    let clock = 1000
    controller = new ExamController(5, {
      open: async () => fakeSession().session,
      now: () => clock,
      read: fakeRead().read,
      uploads: createUploadQueue(),
      pollMs: 50,
      noiseFloorDb: -60,
    })
    await controller.load()
    expect(controller.getView().loadedAt).toBe(1000)
    await vi.waitFor(() => expect(controller?.getView().round?.phase).toBe('preparation'))

    // the server refuses the pause as "not now"; the page reads the round again
    server.use(http.post('/api/rounds/:id/actions', () => HttpResponse.json({ detail: 'Not now.' }, { status: 409 })))
    world.round.paused_ms = 5000
    clock = 4200
    await controller.pause()
    await vi.waitFor(() => expect(controller?.getView().round?.paused_ms).toBe(5000))
    expect(controller.getView().loadedAt).toBe(4200)
    expect(controller.getView().nowMs).toBe(4200)
  })
})

describe('ExamController: the round poll through transient server states', () => {
  const POLL_MS = 50
  const withFollowUps = config({ follow_ups: { enabled: true, prep_seconds: 15, answer_seconds: 60 } })
  const MAIN = attempt({ id: 10, outcome: 'answered', processing_status: 'processing' })
  const FOLLOW = attempt({ id: 11, attempt_kind: 'followup', parent_attempt_id: 10, outcome: 'answered', processing_status: 'processing' })

  const mainProcessing = (followUp: RoundState['follow_up']): RoundState =>
    round({ phase: 'processing', config: withFollowUps, current_attempt: MAIN, follow_up: followUp })
  const followUpQuestion = (): RoundState =>
    round({
      phase: 'question',
      part: 'followup',
      config: withFollowUps,
      current_attempt: attempt({ id: 11, attempt_kind: 'followup', parent_attempt_id: 10 }),
      current: { item_id: 90, question_version_id: 91, text: FOLLOW_UP_TEXT, style: 'short', difficulty: 'recall' },
      follow_up: followUpState('ready'),
    })
  const followUpProcessing = (): RoundState =>
    round({ phase: 'processing', part: 'followup', config: withFollowUps, current_attempt: FOLLOW, follow_up: followUpState('ready') })
  const mainFeedback = (): RoundState =>
    round({ phase: 'feedback', config: withFollowUps, current_attempt: { ...MAIN, processing_status: 'ready' }, follow_up: followUpState('ready') })

  /** GET /rounds/:id answers these states in turn (the last repeats); every result is ready. */
  function script(states: RoundState[]): { reads: () => number } {
    let reads = 0
    server.use(
      http.get('/api/rounds/:id', () => {
        const state = states[Math.min(reads, states.length - 1)]
        reads += 1
        return HttpResponse.json(state)
      }),
      http.get('/api/attempts/:id/result', ({ params }) =>
        HttpResponse.json(readyResult({ attempt_id: Number(params.id), withheld: true, transcript: null, assessment: null })),
      ),
      http.get('/api/subjects/:id', () => HttpResponse.json({ id: 1, name: 'Demokracja ateńska' })),
    )
    return { reads: () => reads }
  }

  function start(clock: () => number): ExamController {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    controller = new ExamController(5, {
      open: async () => fakeSession().session,
      now: clock,
      read: fakeRead().read,
      uploads: createUploadQueue(),
      pollMs: POLL_MS,
      noiseFloorDb: -60,
    })
    return controller
  }

  it('keeps reading the round from "no follow-up yet" through "generating" to the follow-up question', async () => {
    const rounds = script([
      mainProcessing(null),
      mainProcessing(null), // read after the main result is ready: the follow-up has not started yet
      mainProcessing(null),
      mainProcessing(followUpState('generating')),
      mainProcessing(followUpState('generating')),
      followUpQuestion(),
    ])
    const exam = start(() => 1000)
    await exam.load()
    await vi.waitFor(() => expect(exam.getView().round?.part).toBe('followup'), { timeout: 2000, interval: POLL_MS })
    expect(exam.getView().round?.phase).not.toBe('processing')
    expect(rounds.reads()).toBe(6)
  })

  it('keeps reading the round while a ready follow-up is not yet asked', async () => {
    script([mainProcessing(followUpState('ready')), mainProcessing(followUpState('ready')), followUpQuestion()])
    const exam = start(() => 1000)
    await exam.load()
    await vi.waitFor(() => expect(exam.getView().round?.part).toBe('followup'), { timeout: 2000, interval: POLL_MS })
  })

  it('keeps reading the round in the follow-up part until it leaves processing', async () => {
    script([followUpProcessing(), followUpProcessing(), followUpProcessing(), mainFeedback()])
    const exam = start(() => 1000)
    await exam.load()
    await vi.waitFor(() => expect(exam.getView().round?.phase).toBe('feedback'), { timeout: 2000, interval: POLL_MS })
  })

  it('stops after the 3-minute wait with one final read, also before the follow-up has started', async () => {
    const rounds = script([mainProcessing(null)])
    let clock = 1000
    const exam = start(() => clock)
    await exam.load()
    await vi.waitFor(() => expect(rounds.reads()).toBeGreaterThan(3), { timeout: 2000, interval: POLL_MS })
    clock += 181_000
    await vi.waitFor(() => expect(exam.getView().error).toMatch(/taking much longer than usual/), { timeout: 2000, interval: POLL_MS })
    const reads = rounds.reads()
    await vi.advanceTimersByTimeAsync(POLL_MS * 10)
    expect(rounds.reads()).toBe(reads)
    expect(exam.getView().round?.phase).toBe('processing')
  })
})

describe('ExamController: answer again', () => {
  const POLL_MS = 50
  const withFollowUps = config({ immediate_repeat: true, follow_ups: { enabled: true, prep_seconds: 15, answer_seconds: 60 } })
  const REPEAT = attempt({ id: 11, attempt_kind: 'immediate', parent_attempt_id: 10, outcome: 'answered', processing_status: 'processing' })

  function make(): ExamController {
    controller = new ExamController(5, {
      open: async () => fakeSession().session,
      now: () => 1000,
      read: fakeRead().read,
      uploads: createUploadQueue(),
      pollMs: POLL_MS,
      noiseFloorDb: -60,
    })
    return controller
  }

  it('answerAgain() acts only on feedback that offers it', async () => {
    const world = setupRoundWorld({ state: { config: withFollowUps } })
    world.offerAgain = true
    const exam = make()
    await exam.load()
    await exam.answerAgain() // in question / preparation: nothing
    expect(world.requests.filter((r) => (r.body as { action?: string } | undefined)?.action === 'answer_again')).toHaveLength(0)

    world.round = round({
      phase: 'feedback',
      config: withFollowUps,
      current_attempt: attempt({ id: 10, outcome: 'answered', processing_status: 'ready' }),
      can_answer_again: true,
    })
    controller?.dispose()
    const again = make()
    await again.load()
    await again.answerAgain()
    const sent = world.requests.filter((r) => (r.body as { action?: string } | undefined)?.action === 'answer_again')
    expect(sent).toHaveLength(1)
    expect(sent[0].body).toMatchObject({ from_phase: 'feedback', attempt_id: 10 })
    expect(again.getView().round?.current_attempt?.attempt_kind).toBe('immediate')
  })

  it('a repeat whose question has an omitted follow-up still reads the round until feedback', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    const processing = round({ phase: 'processing', config: withFollowUps, current_attempt: REPEAT, follow_up: followUpState('omitted') })
    const feedback = round({
      phase: 'feedback',
      config: withFollowUps,
      current_attempt: { ...REPEAT, processing_status: 'ready' },
      follow_up: followUpState('omitted'),
    })
    const states = [processing, processing, processing, feedback]
    let reads = 0
    server.use(
      http.get('/api/rounds/:id', () => {
        const state = states[Math.min(reads, states.length - 1)]
        reads += 1
        return HttpResponse.json(state)
      }),
      http.get('/api/attempts/:id/result', ({ params }) => HttpResponse.json(readyResult({ attempt_id: Number(params.id) }))),
      http.get('/api/subjects/:id', () => HttpResponse.json({ id: 1, name: 'Demokracja ateńska' })),
    )
    const exam = make()
    await exam.load()
    await vi.waitFor(() => expect(exam.getView().round?.phase).toBe('feedback'), { timeout: 2000, interval: POLL_MS })
    expect(reads).toBe(4)
  })
})

describe('ExamController: suspension', () => {
  const PRE_ROLL = 800
  const ANSWER_MS = 10_000
  const WALL0 = 1_700_000_000_000

  function wavMs(bytes: number): number {
    return ((bytes - 44) / 2 / 16000) * 1000
  }

  function make(options: { prepSeconds?: number } = {}) {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    const world = setupRoundWorld({ state: { config: config({ prep_seconds: options.prepSeconds ?? 30, answer_seconds: ANSWER_MS / 1000 }) } })
    const clocks = { perf: 1000, wall: WALL0 }
    let captureAt = 0
    const mic = fakeSession({
      startRecording: vi.fn(() => {
        captureAt = clocks.perf
      }),
      stop: vi.fn(async () => {
        const recordedMs = PRE_ROLL + (clocks.perf - captureAt)
        return {
          wav: encodeWav(new Float32Array(Math.round(recordedMs * 16)), 16000),
          durationMs: recordedMs,
          events: { ...CAPTURE_EVENTS, captureStartAt: captureAt, stoppedAt: clocks.perf, preRollMs: PRE_ROLL, autoStarted: false },
        }
      }),
    })
    const enqueued: { attemptId: number; events: AttemptEventsIn; wav: Blob; roundId?: number }[] = []
    const uploads: UploadQueue = {
      enqueue: (attemptId, events, wav, roundId) => void enqueued.push({ attemptId, events, wav, roundId }),
      retry: async () => false,
      pending: () => 0,
      failed: () => [],
      subscribe: () => () => {},
      restore: async () => 0,
    }
    controller = new ExamController(5, {
      open: async () => mic.session,
      now: () => clocks.perf,
      wallNow: () => clocks.wall,
      read: fakeRead().read,
      uploads,
      pollMs: 50,
      noiseFloorDb: -60,
    })
    const unbind = bindWake(controller)
    onTestFinished(unbind)
    const named = (action: string) => world.requests.filter((r) => (r.body as { action?: string } | undefined)?.action === action)
    return { world, clocks, mic, enqueued, exam: controller, named }
  }

  async function recording(ctx: ReturnType<typeof make>) {
    await ctx.exam.load()
    await vi.waitFor(() => expect(ctx.exam.getView().prepDeadline).not.toBeNull())
    ctx.exam.answerNow()
    await vi.waitFor(() => expect(ctx.named('start_answer')).toHaveLength(1))
    await vi.waitFor(() => expect(ctx.exam.getView().round?.phase).toBe('recording'))
  }

  async function expectCutAnswer(ctx: ReturnType<typeof make>) {
    await vi.waitFor(() => expect(ctx.enqueued).toHaveLength(1))
    expect(ctx.mic.session.stop).toHaveBeenCalledTimes(1)
    expect(ctx.named('stop')).toHaveLength(1)
    const [upload] = ctx.enqueued
    expect(upload.roundId).toBe(5)
    expect(wavMs(upload.wav.size)).toBeLessThanOrEqual(ANSWER_MS + PRE_ROLL)
    expect(upload.events.stopped_at).toBe(upload.events.capture_start_at + ANSWER_MS)
  }

  it('finishes a recording past its deadline on visibilitychange, cut to the answer time', async () => {
    const ctx = make()
    await recording(ctx)
    ctx.clocks.perf += 25_000
    ctx.clocks.wall += 25_000
    document.dispatchEvent(new Event('visibilitychange'))
    await expectCutAnswer(ctx)
  })

  it('waits FINISH_RETRY_MS before retrying a failed stop at the deadline, not every tick', async () => {
    const ctx = make()
    await recording(ctx)
    let stops = 0
    server.use(
      http.post('/api/rounds/:id/actions', async ({ request }) => {
        const body = (await request.clone().json()) as { action?: string }
        if (body.action !== 'stop') return undefined
        stops += 1
        return HttpResponse.json({ detail: 'Not now.' }, { status: 409 })
      }),
    )
    ctx.clocks.perf += 25_000
    ctx.clocks.wall += 25_000
    await vi.advanceTimersByTimeAsync(100)
    await vi.waitFor(() => expect(ctx.exam.getView().finishRetryAt).not.toBeNull())
    expect(stops).toBe(1)
    for (let i = 0; i < 10; i++) {
      ctx.clocks.perf += 100
      ctx.clocks.wall += 100
      await vi.advanceTimersByTimeAsync(100)
    }
    expect(stops).toBe(1)
    for (let i = 0; i < 20; i++) {
      ctx.clocks.perf += 100
      ctx.clocks.wall += 100
      await vi.advanceTimersByTimeAsync(100)
    }
    await vi.waitFor(() => expect(stops).toBe(2))
    expect(ctx.mic.session.stop).toHaveBeenCalledTimes(1)
  })

  it('does the same on pageshow', async () => {
    const ctx = make()
    await recording(ctx)
    ctx.clocks.perf += 25_000
    ctx.clocks.wall += 25_000
    window.dispatchEvent(new Event('pageshow'))
    await expectCutAnswer(ctx)
  })

  it('does the same on the 100 ms tick alone, without any event', async () => {
    const ctx = make()
    await recording(ctx)
    ctx.clocks.perf += 25_000
    ctx.clocks.wall += 25_000
    await vi.advanceTimersByTimeAsync(100)
    await expectCutAnswer(ctx)
  })

  it('starts the answer by the timer when preparation ran out while asleep', async () => {
    const ctx = make()
    await ctx.exam.load()
    await vi.waitFor(() => expect(ctx.exam.getView().prepDeadline).not.toBeNull())
    ctx.clocks.perf += 40_000
    ctx.clocks.wall += 40_000
    window.dispatchEvent(new Event('pageshow'))
    await vi.waitFor(() => expect(ctx.named('start_answer')).toHaveLength(1))
    expect(ctx.named('start_answer')[0].body).toMatchObject({ start_reason: 'timer' })
  })

  it('finishes when only the wall clock moved (the page clock froze during sleep)', async () => {
    const ctx = make()
    await recording(ctx)
    ctx.clocks.wall += 25_000
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(ctx.enqueued).toHaveLength(1))
    expect(ctx.named('stop')).toHaveLength(1)
  })

  it('re-arms the timers to the true remainder when it wakes with time left', async () => {
    const ctx = make()
    await recording(ctx)
    ctx.clocks.perf += 4_000
    ctx.clocks.wall += 4_000
    const sent = ctx.world.requests.length
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(5_800)
    expect(ctx.world.requests.length).toBe(sent)
    expect(ctx.mic.session.stop).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(400)
    await vi.waitFor(() => expect(ctx.named('stop')).toHaveLength(1))
  })

  it('lets an earlier server answer deadline win, read through the server offset', async () => {
    const ctx = make()
    // the server clock runs 3 s ahead; its deadline falls 4 s after the answer starts
    const serverNow = () => new Date(ctx.clocks.wall + 3_000).toISOString()
    ctx.world.round.clock = {
      server_now: serverNow(),
      phase_started_at: null,
      prep_deadline_at: null,
      answer_deadline_at: new Date(WALL0 + 3_000 + 4_000).toISOString(),
      paused_at: null,
    }
    await recording(ctx)
    ctx.clocks.perf += 5_000
    ctx.clocks.wall += 5_000
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(ctx.named('stop')).toHaveLength(1))
  })
})
