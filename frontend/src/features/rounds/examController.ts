import { ApiError } from '../../api/client'
import { openListeningSession, type CaptureEvents, type ListeningSession } from '../recording/capture'
import {
  act,
  getResult,
  getRound,
  giveUpAttempt,
  recoverAttempt,
  retryAttempt,
  retryFollowUp as postFollowUpRetry,
  type ActionName,
  type AttemptEventsIn,
  type AttemptResult,
  type RecoveryAction,
  type RoundState,
  type StartReason,
} from './api'
import { startReading, type Reading } from './questionAudio'
import { remainingMs, trimWav } from './recovery'
import { timersOf } from './setup'
import { roundStrings } from './strings'
import type { UploadQueue } from './uploads'

const t = roundStrings.exam

export const LEVEL_BARS = 8
const TICK_MS = 100
/** Stop polling for a result after this long and say so. */
const MAX_RESULT_WAIT_MS = 5 * 60 * 1000
/** How long the page waits for a follow-up being prepared before it reads the round once more and stops. */
const MAX_FOLLOW_UP_WAIT_MS = 180 * 1000
/** A refused "stop" is tried again after this long. */
const FINISH_RETRY_MS = 2000
/** Level range shown by the bars: from the noise floor up by this many dB. */
const LEVEL_RANGE_DB = 40
/** A recording longer than its answer time plus the pre-roll by more than this is cut. */
const TRIM_SLACK_MS = 250
/** Clocks that disagree by less than this are taken to agree. */
const CLOCK_TOLERANCE_MS = 50
/** The question waits this long for its voice before it shows without it. */
export const VOICE_HOLD_MS = 10_000

export interface ExamDeps {
  open: typeof openListeningSession
  now(): number
  read: typeof startReading
  uploads: UploadQueue
  pollMs: number
  noiseFloorDb: number
  /** The wall clock (Date.now()), which keeps running while the computer sleeps. */
  wallNow?: () => number
  /** Starts preparing the voice of the questions a round state names (current and upcoming). */
  prefetch?: (state: RoundState) => void
}

export interface ExamView {
  round: RoundState | null
  loadError: string | null
  /** An action or upload problem the learner should see. */
  error: string | null
  /** The question could not be read aloud (info, not an error). */
  voiceFailed: boolean
  /** The server's reason for that, when it gave one (the spending ceiling). */
  voiceMessage?: string | null
  busy: boolean
  /** The page clock at the last tick; every displayed time is computed from it. */
  nowMs: number
  /** The page clock when `round` was last read from the server. */
  loadedAt: number
  prepDeadline: number | null
  answerDeadline: number | null
  listening: 'pending' | 'on' | 'off'
  reading: boolean
  /** The voice is being made: the reading has not started playing yet. */
  voicePreparing: boolean
  /** The question text is on screen; it waits for its voice (up to VOICE_HOLD_MS) so both start together. */
  questionShown: boolean
  /** 0..1 per bar, oldest first. */
  levels: number[]
  result: AttemptResult | null
  retrying: boolean
  startReason: StartReason | null
  /** How long the preparation lasted before the answer started. */
  prepUsedMs: number | null
  /** Length of the answer the last pause kept (not scored), when this page recorded it. */
  interruptedMs: number | null
  /** The page clock when a refused "stop" is tried again by itself; null when none is waiting. */
  finishRetryAt: number | null
  /** "Try again" for a follow-up that could not be prepared is on its way. */
  retryingFollowUp: boolean
  /** The round was paused on this page (not by a reload, Home or another tab). */
  pausedHere: boolean
  /** Recovery found that the cut-off answer's paid call may already have run. */
  mayHaveCharged: boolean
  /** "Give up this answer" for the current failed answer is on its way. */
  givingUp: boolean
}

interface Runtime {
  attemptId: number
  session: ListeningSession | null
  sessionPromise: Promise<ListeningSession | null>
  unsubscribeLevel: (() => void) | null
  shownAt: number
  readyAt: number | null
  playbackMs: number
  reading: Reading | null
  revealTimer: ReturnType<typeof setTimeout> | null
  prepStarted: boolean
  prepStartAt: number | null
  prepTimer: ReturnType<typeof setTimeout> | null
  answerTimer: ReturnType<typeof setTimeout> | null
  /** The deadlines on the wall clock, next to the page-clock ones in the view. */
  prepWallDeadline: number | null
  answerWallDeadline: number | null
  /** Answer time started (by speech, the button or the timer). */
  started: boolean
  startReason: StartReason | null
  captureStartAt: number | null
  listeningAvailable: boolean
  startPromise: Promise<RoundState | null> | null
  stopped: { wav: Blob; durationMs: number; events: CaptureEvents } | null
  /** The stopped recording was already cut to the answer time (or needed no cut). */
  trimmed: boolean
  finishing: boolean
}

function flatLevels(): number[] {
  return Array.from({ length: LEVEL_BARS }, () => 0)
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : t.errors.unknown
}

/** The server's deadlines for one attempt, on its clock (epoch ms). */
interface ServerClock {
  /** Server clock minus the local wall clock, taken on every round read. */
  offsetMs: number
  attemptId: number | null
  prepDeadline: number | null
  answerDeadline: number | null
}

const parseTime = (value: string | null | undefined): number | null => {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isNaN(ms) ? null : ms
}

/**
 * Wakes the controller whenever the page may have slept: back to visible, restored from the
 * back-forward cache, focused, or resumed after being frozen. Returns the remover.
 */
export function bindWake(controller: ExamController, doc: Document = document, win: Window = window): () => void {
  const wake = () => controller.wake()
  const visible = () => {
    if (doc.visibilityState !== 'hidden') wake()
  }
  doc.addEventListener('visibilitychange', visible)
  doc.addEventListener('resume', wake)
  win.addEventListener('pageshow', wake)
  win.addEventListener('focus', wake)
  return () => {
    doc.removeEventListener('visibilitychange', visible)
    doc.removeEventListener('resume', wake)
    win.removeEventListener('pageshow', wake)
    win.removeEventListener('focus', wake)
  }
}

export class ExamController {
  private view: ExamView
  private readonly listeners = new Set<() => void>()
  private rt: Runtime | null = null
  private ticker: ReturnType<typeof setInterval> | null = null
  private pollFor: number | null = null
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private pollSince = 0
  private roundTimer: ReturnType<typeof setTimeout> | null = null
  /** The page clock when the current follow-up was first seen being prepared; null when not waiting. */
  private followUpSince: number | null = null
  private chain: Promise<unknown> = Promise.resolve()
  private busyCount = 0
  private levelDb: number
  private disposed = false
  private loading: Promise<void> | null = null
  private readonly staleHandled = new Set<string>()
  private server: ServerClock | null = null
  /** The first round read of this page: a processing answer may have been cut off by a restart. */
  private freshLoad = true
  /** Attempts recovery was asked about once already. */
  private readonly recovered = new Set<number>()

  constructor(
    readonly roundId: number,
    private readonly deps: ExamDeps,
  ) {
    this.levelDb = deps.noiseFloorDb
    this.view = {
      round: null,
      loadError: null,
      error: null,
      voiceFailed: false,
      busy: false,
      nowMs: deps.now(),
      loadedAt: deps.now(),
      prepDeadline: null,
      answerDeadline: null,
      listening: 'pending',
      reading: false,
      voicePreparing: false,
      questionShown: true,
      levels: flatLevels(),
      result: null,
      retrying: false,
      startReason: null,
      prepUsedMs: null,
      interruptedMs: null,
      finishRetryAt: null,
      retryingFollowUp: false,
      pausedHere: false,
      mayHaveCharged: false,
      givingUp: false,
    }
  }

  // ---- store ----

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getView = (): ExamView => this.view

  private set(patch: Partial<ExamView>): void {
    if (this.disposed) return
    this.view = { ...this.view, ...patch }
    for (const listener of this.listeners) listener()
  }

  // ---- loading and phase entry ----

  /** Reads the round and starts its phase. Safe to call again (StrictMode): one read at a time. */
  load(): Promise<void> {
    this.disposed = false
    if (!this.loading) {
      this.loading = getRound(this.roundId)
        .then((state) => {
          if (!this.disposed) this.enter(state)
        })
        .catch((error: unknown) => this.set({ loadError: errorText(error) }))
        .finally(() => {
          this.loading = null
        })
    }
    return this.loading
  }

  private wallNow(): number {
    return (this.deps.wallNow ?? Date.now)()
  }

  /** The server's clock as this read gives it; the offset is taken anew on every read. */
  private readServerClock(state: RoundState): void {
    const attemptId = state.current_attempt?.id ?? null
    const clock = state.clock
    const serverNow = parseTime(clock?.server_now)
    if (clock && serverNow !== null) {
      this.server = {
        offsetMs: serverNow - this.wallNow(),
        attemptId,
        prepDeadline: parseTime(clock.prep_deadline_at),
        answerDeadline: parseTime(clock.answer_deadline_at),
      }
    } else if (this.server && this.server.attemptId !== attemptId) {
      this.server = { ...this.server, attemptId, prepDeadline: null, answerDeadline: null }
    }
  }

  /** Takes a round state from the server and starts what its phase needs. */
  private enter(state: RoundState): void {
    if (this.disposed) return
    this.readServerClock(state)
    try {
      this.deps.prefetch?.(state)
    } catch {
      // preparing ahead is only a help
    }
    const fresh = this.freshLoad
    this.freshLoad = false
    this.set({
      round: state,
      loadedAt: this.deps.now(),
      nowMs: this.deps.now(),
      ...(state.status !== 'paused' && this.view.pausedHere ? { pausedHere: false } : {}),
    })
    const attempt = state.current_attempt
    if (state.status !== 'active' || !attempt) {
      this.releaseRuntime()
      this.stopPolling()
      this.syncFollowUpWait(state)
      this.syncTicker()
      return
    }
    const mine = this.rt !== null && this.rt.attemptId === attempt.id
    this.syncFollowUpWait(state)
    switch (state.phase) {
      case 'question':
        this.stopPolling()
        if (!mine) this.beginQuestion(state)
        break
      case 'preparation':
        this.stopPolling()
        if (!mine) void this.pauseStale()
        else if (this.rt && !this.rt.prepStarted) this.beginPreparation(state)
        break
      case 'recording':
        this.stopPolling()
        if (!mine) void this.pauseStale()
        break
      case 'processing':
      case 'feedback':
        this.releaseRuntime()
        this.watchResult(attempt.id, fresh && state.phase === 'processing')
        break
      default:
        this.releaseRuntime()
        this.stopPolling()
    }
    this.syncTicker()
  }

  /** A reload or direct visit in the middle of an answer: it cannot be recovered, so the round pauses. */
  private async pauseStale(): Promise<void> {
    const key = `${this.view.round?.current_attempt?.id}:${this.view.round?.phase}`
    this.releaseRuntime()
    // once per attempt and phase: a server that keeps answering the same state must not loop us
    if (this.staleHandled.has(key)) return
    this.staleHandled.add(key)
    await this.run('pause')
  }

  private beginQuestion(state: RoundState): void {
    const attemptId = (state.current_attempt as { id: number }).id
    this.releaseRuntime()
    const rt: Runtime = {
      attemptId,
      session: null,
      sessionPromise: Promise.resolve(null),
      unsubscribeLevel: null,
      shownAt: this.deps.now(),
      readyAt: null,
      playbackMs: 0,
      reading: null,
      revealTimer: null,
      prepStarted: false,
      prepStartAt: null,
      prepTimer: null,
      answerTimer: null,
      prepWallDeadline: null,
      answerWallDeadline: null,
      started: false,
      startReason: null,
      captureStartAt: null,
      listeningAvailable: true,
      startPromise: null,
      stopped: null,
      trimmed: false,
      finishing: false,
    }
    this.rt = rt
    this.set({
      error: null,
      voiceFailed: false,
      voiceMessage: null,
      prepDeadline: null,
      answerDeadline: null,
      listening: 'pending',
      reading: false,
      voicePreparing: false,
      questionShown: state.config.question_audio.mode !== 'premium',
      levels: flatLevels(),
      result: null,
      retrying: false,
      givingUp: false,
      startReason: null,
      prepUsedMs: null,
    })
    rt.sessionPromise = this.openSession(rt)
    if (state.config.question_audio.mode === 'off') {
      void this.questionReady(0)
    } else {
      if (!this.view.questionShown) rt.revealTimer = setTimeout(() => this.reveal(rt), VOICE_HOLD_MS)
      this.startReadingNow(rt, state)
    }
  }

  /** Puts the held question on screen; it counts as shown from now. */
  private reveal(rt: Runtime): void {
    if (rt.revealTimer) clearTimeout(rt.revealTimer)
    rt.revealTimer = null
    if (this.disposed || this.rt !== rt || this.view.questionShown) return
    rt.shownAt = this.deps.now()
    this.set({ questionShown: true })
  }

  private async openSession(rt: Runtime): Promise<ListeningSession | null> {
    let session: ListeningSession
    try {
      session = await this.deps.open({ noiseFloorDb: this.deps.noiseFloorDb })
    } catch {
      return null
    }
    if (this.disposed || this.rt !== rt) {
      session.dispose()
      return null
    }
    rt.session = session
    rt.unsubscribeLevel = session.onLevel((db) => {
      this.levelDb = db
    })
    if (rt.reading) session.setMuted(true)
    return session
  }

  // ---- question ----

  private startReadingNow(rt: Runtime, state: RoundState): void {
    rt.reading?.stop()
    const reading = this.deps.read((state.current as { text: string }).text, { ...state.config.question_audio, language: state.config.language ?? null })
    rt.reading = reading
    rt.session?.setMuted(true)
    const playing = () => {
      if (this.disposed || this.rt !== rt || rt.reading !== reading) return
      this.set({ voicePreparing: false })
      this.reveal(rt)
    }
    if (reading.started) {
      this.set({ reading: true, voicePreparing: true })
      void reading.started.then(playing)
    } else {
      this.set({ reading: true })
      playing()
    }
    void reading.done.then((result) => {
      if (this.disposed || this.rt !== rt || rt.reading !== reading) return
      rt.reading = null
      rt.session?.setMuted(false)
      this.reveal(rt)
      this.set({
        reading: false,
        voicePreparing: false,
        voiceFailed: this.view.voiceFailed || result.failed,
        voiceMessage: result.message ?? this.view.voiceMessage ?? null,
      })
      if (this.view.round?.phase === 'question') void this.questionReady(result.failed ? 0 : result.playbackMs)
    })
  }

  /** "Read it myself, start preparation": stops the voice (which then moves on) or moves on at once. */
  readMyself(): void {
    const rt = this.rt
    if (!rt || this.view.round?.phase !== 'question' || this.busyCount > 0) return
    if (rt.reading) rt.reading.stop()
    else void this.questionReady(0)
  }

  replay(): void {
    const rt = this.rt
    const state = this.view.round
    if (!rt || rt.started || !state || state.config.question_audio.mode === 'off') return
    if (state.phase !== 'question' && state.phase !== 'preparation') return
    this.startReadingNow(rt, state)
  }

  private async questionReady(playbackMs: number): Promise<void> {
    const rt = this.rt
    if (!rt || rt.readyAt !== null) return
    rt.readyAt = this.deps.now()
    rt.playbackMs = playbackMs
    const next = await this.run('question_ready')
    if (!next && this.rt === rt) rt.readyAt = null
  }

  // ---- preparation ----

  private beginPreparation(state: RoundState): void {
    const rt = this.rt as Runtime
    rt.prepStarted = true
    rt.prepStartAt = this.deps.now()
    const prepMs = timersOf(state).prepSeconds * 1000
    if (prepMs === 0) {
      void this.startAnswer('timer')
      return
    }
    const deadline = this.deps.now() + prepMs
    rt.prepWallDeadline = this.wallNow() + prepMs
    this.set({ prepDeadline: deadline, nowMs: this.deps.now() })
    rt.prepTimer = setTimeout(() => void this.startAnswer('timer'), prepMs)
    void this.attachListening(rt)
  }

  private async attachListening(rt: Runtime): Promise<void> {
    const session = await rt.sessionPromise
    if (this.disposed || this.rt !== rt || rt.started) return
    if (!session || !session.available) {
      rt.listeningAvailable = false
      this.set({ listening: 'off' })
      return
    }
    this.set({ listening: 'on' })
    session.startListening((onsetMs) => this.onSpeech(rt, onsetMs))
  }

  private onSpeech(rt: Runtime, onsetMs: number): void {
    if (this.disposed || this.rt !== rt || rt.started) return
    this.beginAnswer(rt, 'auto', onsetMs)
  }

  /** "Answer now" in preparation. */
  answerNow(): void {
    const rt = this.rt
    if (!rt || this.view.round?.phase !== 'preparation' || rt.started || this.busyCount > 0) return
    void this.startAnswer('button')
  }

  /** Starts the recording by hand or by the preparation deadline. */
  private async startAnswer(reason: 'button' | 'timer'): Promise<void> {
    const rt = this.rt
    if (!rt || rt.started) return
    rt.started = true
    if (rt.prepTimer) clearTimeout(rt.prepTimer)
    rt.prepTimer = null
    const session = await rt.sessionPromise
    if (this.disposed || this.rt !== rt) return
    rt.listeningAvailable = !!session?.available
    session?.startRecording()
    this.beginAnswer(rt, reason, this.deps.now())
  }

  /** The answer time runs from the capture start (the speech onset for "auto"). */
  private beginAnswer(rt: Runtime, reason: StartReason, captureStartAt: number): void {
    rt.started = true
    rt.startReason = reason
    rt.captureStartAt = captureStartAt
    if (rt.prepTimer) clearTimeout(rt.prepTimer)
    rt.prepTimer = null
    const answerMs = timersOf(this.view.round as RoundState).answerSeconds * 1000
    const deadline = captureStartAt + answerMs
    rt.prepWallDeadline = null
    rt.answerWallDeadline = this.wallNow() + (deadline - this.deps.now())
    this.set({
      prepDeadline: null,
      answerDeadline: deadline,
      listening: 'off',
      startReason: reason,
      prepUsedMs: rt.prepStartAt === null ? null : Math.max(0, captureStartAt - rt.prepStartAt),
      nowMs: this.deps.now(),
    })
    rt.answerTimer = setTimeout(() => void this.finishAnswer(true), Math.max(0, deadline - this.deps.now()))
    rt.startPromise = this.run('start_answer', { start_reason: reason })
    this.syncTicker()
  }

  // ---- finishing ----

  private eventsOf(rt: Runtime, ev: CaptureEvents, interrupted: boolean): AttemptEventsIn {
    const capture = ev.captureStartAt ?? rt.captureStartAt ?? this.deps.now()
    return {
      question_shown_at: rt.shownAt,
      question_ready_at: rt.readyAt ?? rt.shownAt,
      playback_ms: Math.round(rt.playbackMs),
      prep_end_at: capture,
      start_reason: rt.startReason ?? 'button',
      capture_start_at: capture,
      stopped_at: ev.stoppedAt ?? this.deps.now(),
      pre_roll_ms: Math.round(ev.preRollMs),
      interrupted,
      listening_available: rt.listeningAvailable,
    }
  }

  /** "Finish answer", or the answer deadline (`auto`). */
  async finishAnswer(auto = false): Promise<void> {
    const rt = this.rt
    if (!rt || !rt.started || rt.finishing) return
    if (!auto && this.busyCount > 0) return
    rt.finishing = true
    if (rt.answerTimer) clearTimeout(rt.answerTimer)
    rt.answerTimer = null
    if (this.view.finishRetryAt !== null) this.set({ finishRetryAt: null })
    if (rt.startPromise) {
      const started = await rt.startPromise
      if (!started && this.rt === rt) {
        // the start was refused or lost: try it once more so "stop" has a recording to stop
        await this.run('start_answer', { start_reason: rt.startReason ?? 'button' })
      }
    }
    if (this.disposed || this.rt !== rt) return
    if (!rt.stopped) {
      rt.stopped = rt.session
        ? await rt.session.stop()
        : { wav: new Blob([]), durationMs: 0, events: this.emptyEvents() }
      rt.session?.dispose()
    }
    if (!rt.trimmed) {
      rt.trimmed = true
      rt.stopped = await this.cutToAnswerTime(rt, rt.stopped)
      if (this.disposed || this.rt !== rt) return
    }
    const stopped = rt.stopped
    if (stopped.durationMs === 0) {
      const next = await this.run('mic_failure')
      if (!next && this.rt === rt) this.rearmFinish(rt)
      return
    }
    const events = this.eventsOf(rt, stopped.events, false)
    const next = await this.run('stop')
    if (next) this.deps.uploads.enqueue(rt.attemptId, events, stopped.wav, this.roundId)
    else if (this.rt === rt) this.rearmFinish(rt)
  }

  /**
   * An answer that went on past its deadline (the tab slept while the microphone kept capturing)
   * keeps only its answer time plus the pre-roll; it stops at the deadline.
   */
  private async cutToAnswerTime(rt: Runtime, stopped: NonNullable<Runtime['stopped']>): Promise<NonNullable<Runtime['stopped']>> {
    const round = this.view.round
    if (!round || stopped.durationMs === 0) return stopped
    const answerMs = timersOf(round).answerSeconds * 1000
    const keepMs = answerMs + stopped.events.preRollMs
    if (stopped.durationMs <= keepMs + TRIM_SLACK_MS) return stopped
    const captureStartAt = stopped.events.captureStartAt ?? rt.captureStartAt
    try {
      const wav = await trimWav(stopped.wav, keepMs)
      return {
        wav,
        durationMs: keepMs,
        events: { ...stopped.events, stoppedAt: captureStartAt === null ? stopped.events.stoppedAt : captureStartAt + answerMs },
      }
    } catch {
      return stopped // the server measures what it gets; never lose the answer over a trim
    }
  }

  private emptyEvents(): CaptureEvents {
    return { listeningStartAt: null, speechDetectedAt: null, captureStartAt: null, stoppedAt: null, preRollMs: 0, autoStarted: false }
  }

  /** The action failed: let the learner (or the timer, shortly) finish again. */
  private rearmFinish(rt: Runtime): void {
    rt.finishing = false
    this.set({ finishRetryAt: this.deps.now() + FINISH_RETRY_MS, nowMs: this.deps.now() })
    rt.answerTimer = setTimeout(() => void this.finishAnswer(true), FINISH_RETRY_MS)
  }

  /** "Try now": finishes the answer again at once instead of waiting for the countdown. */
  finishNow(): void {
    if (this.view.finishRetryAt === null) return
    void this.finishAnswer(true)
  }

  /** "I don't know": the audio is discarded. */
  async dontKnow(): Promise<void> {
    const phase = this.view.round?.phase
    if ((phase !== 'preparation' && phase !== 'recording') || this.busyCount > 0) return
    this.releaseRuntime()
    await this.run('dont_know')
  }

  async skip(): Promise<void> {
    const phase = this.view.round?.phase
    if ((phase !== 'question' && phase !== 'preparation') || this.busyCount > 0) return
    this.releaseRuntime()
    await this.run('skip')
  }

  /** Pause: an answer in progress is kept (events + audio) but not scored. */
  async pause(): Promise<void> {
    const state = this.view.round
    if (!state || state.status !== 'active' || state.phase === 'summary' || this.busyCount > 0) return
    const rt = this.rt
    if (rt?.finishing) return
    if (rt) rt.finishing = true
    let upload: { attemptId: number; events: AttemptEventsIn; wav: Blob; durationMs: number } | null = null
    if (rt && rt.started && (state.phase === 'preparation' || state.phase === 'recording')) {
      if (rt.startPromise) await rt.startPromise
      const stopped = rt.stopped ?? (rt.session ? await rt.session.stop() : null)
      if (stopped && stopped.durationMs > 0) {
        upload = {
          attemptId: rt.attemptId,
          events: this.eventsOf(rt, stopped.events, true),
          wav: stopped.wav,
          durationMs: stopped.durationMs,
        }
      }
    }
    this.releaseRuntime()
    const next = await this.run('pause')
    if (next) this.set({ interruptedMs: upload ? upload.durationMs : null, pausedHere: next.status === 'paused' })
    if (next && upload) this.deps.uploads.enqueue(upload.attemptId, upload.events, upload.wav, this.roundId)
  }

  async resume(): Promise<void> {
    if (this.busyCount > 0) return
    await this.run('resume')
  }

  async abandon(): Promise<void> {
    if (this.busyCount > 0) return
    this.releaseRuntime()
    await this.run('abandon')
  }

  async next(): Promise<void> {
    if (this.view.round?.phase !== 'feedback' || this.busyCount > 0 || this.view.givingUp) return
    await this.run('next')
  }

  /** "Answer again": the same main question once more, from its practice feedback. */
  async answerAgain(): Promise<void> {
    const state = this.view.round
    if (state?.phase !== 'feedback' || !state.can_answer_again || this.busyCount > 0) return
    await this.run('answer_again')
  }

  /** "Continue without follow-up": also while it is being prepared, and after a failed main answer. */
  async noFollowUp(): Promise<void> {
    const state = this.view.round
    if (state?.phase !== 'processing' || state.part !== 'main' || this.busyCount > 0 || this.view.givingUp) return
    await this.run('no_follow_up')
  }

  /** "Try again" for a follow-up that could not be prepared. */
  async retryFollowUp(): Promise<void> {
    const followUp = this.view.round?.follow_up
    if (!followUp || followUp.status !== 'failed' || this.view.retryingFollowUp) return
    this.set({ retryingFollowUp: true, error: null })
    try {
      await postFollowUpRetry(followUp.id)
      const state = await getRound(this.roundId)
      if (this.disposed) return
      this.followUpSince = null
      this.set({ retryingFollowUp: false })
      this.enter(state)
    } catch (error) {
      this.set({ retryingFollowUp: false, error: errorText(error) })
      if (error instanceof ApiError && error.status === 409) void this.resync()
    }
  }

  // ---- actions ----

  /** Runs one round action after the ones before it; `from_phase` and `attempt_id` are read when it runs. */
  private run(action: ActionName, extra: { start_reason?: StartReason } = {}): Promise<RoundState | null> {
    this.busyCount += 1
    this.set({ busy: true })
    const result = this.chain.then(async () => {
      const state = this.view.round
      if (this.disposed || !state) return null
      try {
        const next = await act(this.roundId, {
          action,
          from_phase: state.phase,
          ...(state.current_attempt ? { attempt_id: state.current_attempt.id } : {}),
          ...extra,
        })
        this.set({ error: null })
        this.enter(next)
        return next
      } catch (error) {
        this.set({ error: errorText(error) })
        if (error instanceof ApiError && error.status === 409) void this.resync()
        return null
      }
    })
    this.chain = result.finally(() => {
      this.busyCount -= 1
      this.set({ busy: this.busyCount > 0 })
    })
    return result
  }

  /** The server and the page disagree: read the round again. */
  private async resync(): Promise<void> {
    try {
      const state = await getRound(this.roundId)
      if (!this.disposed && state.current_attempt?.id === this.view.round?.current_attempt?.id && state.phase === this.view.round?.phase) {
        // paused_ms continues from this read, so the readout's clock restarts with it
        this.set({ round: state, loadedAt: this.deps.now(), nowMs: this.deps.now() })
      } else if (!this.disposed) {
        this.enter(state)
      }
    } catch {
      // the error notice already says what went wrong
    }
  }

  // ---- processing and feedback ----

  private stopPolling(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollTimer = null
    this.pollFor = null
  }

  private stopRoundPolling(): void {
    if (this.roundTimer) clearTimeout(this.roundTimer)
    this.roundTimer = null
  }

  /**
   * Whether the round itself must be read again while it stays in processing. The result poll
   * stops once the (withheld) main result is ready, but the server may still be between states:
   * the follow-up is being prepared, not yet started (`null`) or ready but not yet asked. In the
   * follow-up part the round waits in processing until its answer moves the round on.
   */
  private awaitingRound(state: RoundState): boolean {
    if (state.status !== 'active' || state.phase !== 'processing') return false
    if (state.part === 'followup') return true
    if (state.part !== 'main') return false
    const result = this.view.result
    const mainReady =
      result !== null && result.attempt_id === state.current_attempt?.id && result.status === 'ready'
    // a repeat (Answer again) has no follow-up of its own: once ready, the round moves to feedback
    if (state.current_attempt?.attempt_kind === 'immediate') return mainReady
    const followUp = state.follow_up?.status ?? null
    if (followUp === 'generating') return true
    return mainReady && (followUp === null || followUp === 'ready')
  }

  /** Reads the round again, on its own clock, until it moves on or the wait is over. */
  private syncFollowUpWait(state: RoundState): void {
    const waiting = this.awaitingRound(state)
    if (!waiting) {
      this.followUpSince = null
      this.stopRoundPolling()
      return
    }
    if (this.followUpSince === null) this.followUpSince = this.deps.now()
    if (!this.roundTimer) this.roundTimer = setTimeout(() => void this.pollRound(), this.deps.pollMs)
  }

  private async pollRound(): Promise<void> {
    this.roundTimer = null
    if (this.disposed || this.followUpSince === null) return
    const overdue = this.deps.now() - this.followUpSince > MAX_FOLLOW_UP_WAIT_MS
    try {
      const state = await getRound(this.roundId)
      if (this.disposed) return
      if (overdue && this.awaitingRound(state)) {
        // one final read; the ways out ("Continue without follow-up") stay on screen
        this.set({ round: state, loadedAt: this.deps.now(), nowMs: this.deps.now(), error: t.errors.resultSlow })
        this.followUpSince = null
        return
      }
      this.enter(state)
    } catch {
      if (!this.disposed && !overdue) this.roundTimer = setTimeout(() => void this.pollRound(), this.deps.pollMs)
    }
  }

  /** `recoverFirst`: a page opened on a processing answer asks the server to recover it before polling. */
  private watchResult(attemptId: number, recoverFirst = false): void {
    if (this.pollFor === attemptId) return
    this.stopPolling()
    this.pollFor = attemptId
    this.pollSince = this.deps.now()
    this.set({ result: null, retrying: false, mayHaveCharged: false })
    if (recoverFirst) void this.recover(attemptId).then(() => this.poll(attemptId))
    else void this.poll(attemptId)
  }

  /** Once per attempt: resumes a safe cut-off, or tells whether a paid call may already have run. */
  private async recover(attemptId: number): Promise<RecoveryAction | null> {
    this.recovered.add(attemptId)
    try {
      const out = await recoverAttempt(attemptId)
      if (!this.disposed && out.action === 'confirm') this.set({ mayHaveCharged: true })
      return out.action
    } catch {
      return null // the result shows what the server knows; "Try again" stays
    }
  }

  private async poll(attemptId: number): Promise<void> {
    if (this.disposed || this.pollFor !== attemptId) return
    try {
      const result = await getResult(attemptId)
      if (this.disposed || this.pollFor !== attemptId) return
      if (result.status === 'failed' && result.failure?.kind === 'interrupted' && !this.recovered.has(attemptId)) {
        // cut off by a restart: the server resumes it by itself when no paid call can repeat
        const action = await this.recover(attemptId)
        if (this.disposed || this.pollFor !== attemptId) return
        if (action === 'resumed' || action === 'running' || action === 'completed') {
          this.pollAgain(attemptId)
          return
        }
      }
      this.set({ result, retrying: false })
      if (result.status === 'processing') {
        this.pollAgain(attemptId)
      } else if (this.view.round?.phase === 'processing') {
        const state = await getRound(this.roundId)
        if (!this.disposed && this.pollFor === attemptId) this.enter(state)
      }
    } catch (error) {
      if (this.disposed || this.pollFor !== attemptId) return
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        // the server will not answer this differently next time
        this.stopPolling()
        this.set({ error: errorText(error) })
        return
      }
      this.pollAgain(attemptId)
    }
  }

  /** Poll again shortly, unless the result has been out of reach for too long. */
  private pollAgain(attemptId: number): void {
    if (this.deps.now() - this.pollSince > MAX_RESULT_WAIT_MS) {
      this.stopPolling()
      this.set({ error: t.errors.resultSlow })
      return
    }
    this.pollTimer = setTimeout(() => void this.poll(attemptId), this.deps.pollMs)
  }

  /** "Try again" after a technical failure: the server starts the processing again. */
  async retryResult(): Promise<void> {
    const attemptId = this.view.round?.current_attempt?.id
    if (attemptId === undefined || this.view.retrying || this.view.givingUp) return
    this.set({ retrying: true, error: null, mayHaveCharged: false })
    try {
      const result = await retryAttempt(attemptId)
      if (this.disposed) return
      this.stopPolling()
      this.pollFor = attemptId
      this.set({ result, retrying: false })
      this.pollTimer = setTimeout(() => void this.poll(attemptId), this.deps.pollMs)
    } catch (error) {
      this.set({ retrying: false, error: errorText(error) })
    }
  }

  /**
   * "Give up this answer" for the current failed main answer: it is then not assessed. The round
   * moves as if its result had arrived (the server settles it), so the round is read again.
   */
  async giveUpResult(): Promise<void> {
    const attemptId = this.view.round?.current_attempt?.id
    if (attemptId === undefined || this.view.givingUp || this.view.retrying || this.busyCount > 0) return
    if (this.view.round?.part !== 'main') return
    this.set({ givingUp: true, error: null, mayHaveCharged: false })
    let result: AttemptResult
    try {
      result = await giveUpAttempt(attemptId)
    } catch (error) {
      if (this.disposed) return
      this.set({ givingUp: false, error: error instanceof Error ? error.message : roundStrings.summary.giveUpFailed })
      if (error instanceof ApiError && error.status === 409) {
        // show what the server has; the old result stays on screen until the new one arrives
        this.stopPolling()
        this.pollFor = attemptId
        void this.poll(attemptId)
      }
      return
    }
    if (this.disposed) return
    this.stopPolling()
    this.pollFor = attemptId
    this.set({ result, givingUp: false })
    try {
      let state = await getRound(this.roundId)
      if (this.disposed) return
      if (state.status === 'active' && state.phase === 'processing' && state.current_attempt?.id === attemptId) {
        // the server's move was lost: recovery settles it
        await recoverAttempt(attemptId).catch(() => undefined)
        state = await getRound(this.roundId)
        if (this.disposed) return
      }
      // the same attempt in feedback: watchResult keeps the given-up result
      this.enter(state)
    } catch {
      // the answer was given up; only the round read failed
      void this.resync()
    }
  }

  // ---- ticking and cleanup ----

  /** The clock and the level bars tick while something timed is on screen. */
  private syncTicker(): void {
    const state = this.view.round
    const needed =
      !this.disposed &&
      state !== null &&
      (this.view.prepDeadline !== null || this.view.answerDeadline !== null || state.status === 'paused')
    if (needed && !this.ticker) {
      this.ticker = setInterval(() => this.tick(), TICK_MS)
    } else if (!needed && this.ticker) {
      clearInterval(this.ticker)
      this.ticker = null
    }
  }

  /** The page may have slept: act on any deadline that passed, re-arm the timers to the rest. */
  wake(): void {
    if (this.disposed) return
    this.checkDeadlines(true)
    this.set({ nowMs: this.deps.now() })
  }

  /** The deadline remainder by the page clock, the wall clock and the server's clock. */
  private remaining(kind: 'prep' | 'answer'): number {
    const rt = this.rt
    const server = this.server && rt && this.server.attemptId === rt.attemptId ? this.server : null
    return remainingMs(
      {
        perfDeadline: kind === 'prep' ? this.view.prepDeadline : this.view.answerDeadline,
        wallDeadline: (kind === 'prep' ? rt?.prepWallDeadline : rt?.answerWallDeadline) ?? null,
        serverDeadline: server ? (kind === 'prep' ? server.prepDeadline : server.answerDeadline) : null,
        offsetMs: server?.offsetMs ?? 0,
      },
      this.deps.now(),
      this.wallNow(),
    )
  }

  private checkDeadlines(rearm: boolean): void {
    const rt = this.rt
    if (!rt) return
    // while a failed finish waits for its retry (rearmFinish), its own timer owns the answer
    const retrying = this.view.finishRetryAt !== null
    const kind = !rt.started && rt.prepStarted && this.view.prepDeadline !== null ? 'prep' : rt.started && !rt.finishing && !retrying && this.view.answerDeadline !== null ? 'answer' : null
    if (kind === null) return
    const left = this.remaining(kind)
    if (left <= 0) {
      if (kind === 'prep') void this.startAnswer('timer')
      else void this.finishAnswer(true)
      return
    }
    const perfDeadline = (kind === 'prep' ? this.view.prepDeadline : this.view.answerDeadline) as number
    const truer = left < perfDeadline - this.deps.now() - CLOCK_TOLERANCE_MS
    if (truer) {
      // another clock knows the deadline is nearer: the readout follows it
      this.set(kind === 'prep' ? { prepDeadline: this.deps.now() + left } : { answerDeadline: this.deps.now() + left })
    }
    if (!rearm && !truer) return
    if (kind === 'prep') {
      if (rt.prepTimer) clearTimeout(rt.prepTimer)
      rt.prepTimer = setTimeout(() => void this.startAnswer('timer'), left)
    } else {
      if (rt.answerTimer) clearTimeout(rt.answerTimer)
      rt.answerTimer = setTimeout(() => void this.finishAnswer(true), left)
    }
  }

  private tick(): void {
    this.checkDeadlines(false)
    if (this.disposed) return
    const session = this.rt?.session
    const live = !!session && session.available && (this.view.listening === 'on' || !!this.rt?.started)
    let levels = this.view.levels
    if (live) {
      const level = Math.max(0, Math.min(1, (this.levelDb - this.deps.noiseFloorDb) / LEVEL_RANGE_DB))
      levels = [...levels.slice(1), level]
    } else if (levels.some((value) => value !== 0)) {
      levels = flatLevels()
    }
    this.set({ nowMs: this.deps.now(), levels })
  }

  /** Stops the timers, the voice and the microphone of the current question. */
  private releaseRuntime(): void {
    const rt = this.rt
    if (!rt) return
    this.rt = null
    if (rt.prepTimer) clearTimeout(rt.prepTimer)
    if (rt.answerTimer) clearTimeout(rt.answerTimer)
    if (rt.revealTimer) clearTimeout(rt.revealTimer)
    rt.reading?.stop()
    rt.unsubscribeLevel?.()
    rt.session?.dispose()
    // a session still being opened is disposed by openSession when it arrives
    this.set({
      prepDeadline: null,
      answerDeadline: null,
      listening: 'pending',
      reading: false,
      voicePreparing: false,
      questionShown: true,
      levels: flatLevels(),
      finishRetryAt: null,
    })
    this.syncTicker()
  }

  dispose(): void {
    this.releaseRuntime()
    this.stopPolling()
    this.stopRoundPolling()
    if (this.ticker) clearInterval(this.ticker)
    this.ticker = null
    this.disposed = true
  }
}
