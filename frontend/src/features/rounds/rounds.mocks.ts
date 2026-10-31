import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import type {
  ActionBody,
  AttemptEventsIn,
  AttemptOut,
  AttemptResult,
  FeedbackTiming,
  FollowUpState,
  ItemOut,
  Phase,
  RecoveryAction,
  RoundConfig,
  RoundState,
  RoundSummary,
} from './api'

/** Polish questions of short (≤ 120), medium (121–240) and long (> 240) length. */
export const QUESTIONS = [
  'Czym był ostracyzm w Atenach?',
  'Wyjaśnij, w jaki sposób Klejstenes zreformował podział obywateli Aten i dlaczego ta zmiana osłabiła wpływy dawnych rodów arystokratycznych w zgromadzeniu ludowym.',
  'Omów rozwój demokracji ateńskiej od reform Solona do czasów Peryklesa. W odpowiedzi uwzględnij rolę Rady Pięciuset, zgromadzenia ludowego i sądów ludowych, wyjaśnij znaczenie diet dla ubogich obywateli oraz wskaż ograniczenia ateńskiej demokracji, takie jak wykluczenie kobiet, metojków i niewolników.',
]

export function config(patch: Partial<RoundConfig> = {}): RoundConfig {
  return {
    round_type: 'standard',
    scope: { kind: 'subject', topic_ids: [], document_ids: [] },
    source_policy: 'materials',
    feedback_timing: 'practice',
    question_count: 3,
    style: 'mixed',
    difficulty: 'mixed',
    prep_seconds: 30,
    answer_seconds: 120,
    question_audio: { mode: 'off' },
    follow_ups: { enabled: false, prep_seconds: 15, answer_seconds: 60 },
    band_bounds: [30, 60, 80],
    factual_weights: [30, 50],
    ...patch,
  }
}

export function attempt(patch: Partial<AttemptOut> = {}): AttemptOut {
  return {
    id: 10,
    attempt_kind: 'initial',
    outcome: 'pending',
    processing_status: null,
    parent_attempt_id: null,
    start_reason: null,
    recording_id: null,
    ...patch,
  }
}

export function round(patch: Partial<RoundState> = {}, timing: FeedbackTiming = 'practice'): RoundState {
  const items: ItemOut[] = QUESTIONS.map((_, i) => ({
    id: 50 + i,
    ordinal: i + 1,
    role: 'main',
    question_version_id: 70 + i,
    status: i === 0 ? 'current' : 'pending',
    latest_attempt: i === 0 ? attempt() : null,
  }))
  return {
    id: 5,
    subject_id: 1,
    status: 'active',
    phase: 'question',
    part: 'main',
    current_index: 0,
    total: QUESTIONS.length,
    config: config({ feedback_timing: timing }),
    current: { item_id: 50, question_version_id: 70, text: QUESTIONS[0], style: 'open', difficulty: 'recall' },
    current_attempt: attempt(),
    items,
    pause_count: 0,
    paused_ms: 0,
    follow_up: null,
    ...patch,
  }
}

export const FOLLOW_UP_TEXT = 'Wspomniałeś o głosowaniu na skorupach. Wyjaśnij, kogo w ten sposób wygnano i dlaczego.'

export function followUpState(status: FollowUpState['status'], patch: Partial<FollowUpState> = {}): FollowUpState {
  return {
    id: 7,
    status,
    failure: status === 'failed' ? { kind: 'server', message: 'The provider is not responding.' } : null,
    retryable: status === 'failed',
    ...patch,
  }
}

export function result(patch: Partial<AttemptResult> = {}): AttemptResult {
  return {
    attempt_id: 10,
    outcome: 'answered',
    status: 'processing',
    stage: 'transcribing',
    recording: { id: 31, duration_ms: 12000 },
    failure: null,
    withheld: false,
    transcript: null,
    metrics: null,
    assessment: null,
    ...patch,
  }
}

export function readyResult(patch: Partial<AttemptResult> = {}): AttemptResult {
  return result({
    status: 'ready',
    stage: 'done',
    transcript: 'Ostracyzm był głosowaniem na skorupkach.',
    assessment: {
      status: 'assessed',
      band: 'good',
      scores: { correctness: 71.4, completeness: 58.2, factual: 64.6, language: 70, total: 67.4 },
    },
    ...patch,
  })
}

/** An answer given up: final, never scored. */
export function givenUpResult(patch: Partial<AttemptResult> = {}): AttemptResult {
  return result({ outcome: 'given_up', status: 'ready', stage: 'not_assessed', failure: null, ...patch })
}

export interface Recorded {
  method: string
  path: string
  body: unknown
}

export interface RoundWorld {
  round: RoundState
  summary: RoundSummary
  requests: Recorded[]
  /** Successive answers of GET /api/attempts/:id/result; the last one repeats. */
  results: AttemptResult[]
  /** Answer of POST /api/attempts/:id/retry. */
  retryResult: AttemptResult
  /** Set to fail POST /rounds with this status and detail. */
  createError: { status: number; detail: unknown } | null
  events: { attemptId: number; body: AttemptEventsIn }[]
  nextAttemptId: number
  pausedFrom: Phase | null
  /** How many times GET /api/attempts/:id/result was called. */
  resultCalls: number
  /**
   * With follow-ups on: the follow-up states GET /rounds/:id shows, one per read, once the main
   * result is in ("ready" asks the follow-up). The last one repeats.
   */
  followUps: FollowUpState['status'][]
  /** What POST /follow-ups/:id/retry turns `followUps` into. */
  retryFollowUps: FollowUpState['status'][]
  /** The main attempt whose follow-up is being prepared or asked. */
  mainAttempt: AttemptOut | null
  /** How many times GET /api/rounds/:id was called. */
  roundReads: number
  /** Practice feedback offers "Answer again" once per main question (the round's immediate_repeat). */
  offerAgain: boolean
  /** The question indexes already answered again. */
  repeated: Set<number>
  /** What POST /attempts/:id/recover answers. */
  recoverAction: RecoveryAction
  /** Set to refuse POST /attempts/:id/give-up with this status and detail. */
  giveUpError?: { status: number; detail: string } | null
  /** false: the give-up does not move a round waiting in processing (a lost settle). Default true. */
  giveUpSettles?: boolean
}

const ANSWERING: Phase[] = ['preparation', 'recording']

/** Like the server: "Answer again" on a main answer's practice feedback, once per question. */
export function syncAgain(world: RoundWorld): void {
  const r = world.round
  const current = r.current_attempt
  r.can_answer_again =
    world.offerAgain &&
    r.status === 'active' &&
    r.phase === 'feedback' &&
    r.part === 'main' &&
    current !== null &&
    current.attempt_kind !== 'immediate' &&
    current.outcome !== 'technical_failure' &&
    !world.repeated.has(r.current_index)
}

/** The round's phase table, as far as the exam screen needs it. */
export function applyAction(world: RoundWorld, body: ActionBody): void {
  applyPhase(world, body)
  syncAgain(world)
}

function applyPhase(world: RoundWorld, body: ActionBody): void {
  const r = world.round
  const timing = r.config.feedback_timing
  const last = r.current_index === r.total - 1
  const current = r.items[r.current_index]
  const followUps = r.config.follow_ups?.enabled === true
  const setOutcome = (outcome: string, extra: Partial<AttemptOut> = {}) => {
    const next = { ...(r.current_attempt as AttemptOut), outcome, ...extra }
    r.current_attempt = next
    current.latest_attempt = next
  }
  const advance = () => {
    r.part = 'main'
    r.follow_up = null
    if (last) {
      r.status = 'finished'
      r.phase = 'summary'
      return
    }
    r.current_index += 1
    r.phase = 'question'
    const item = r.items[r.current_index]
    current.status = 'done'
    item.status = 'current'
    const fresh = attempt({ id: world.nextAttemptId++ })
    item.latest_attempt = fresh
    r.current_attempt = fresh
    r.current = {
      item_id: item.id,
      question_version_id: item.question_version_id,
      text: QUESTIONS[r.current_index] ?? QUESTIONS[0],
      style: 'open',
      difficulty: 'recall',
    }
  }
  const afterAnswer = () => {
    if (r.part === 'followup') {
      // feedback is on the main answer
      r.part = 'main'
      if (world.mainAttempt) r.current_attempt = world.mainAttempt
    }
    if (timing === 'practice') r.phase = 'feedback'
    else advance()
  }
  const { action } = body
  if (action === 'abandon') {
    if (r.status === 'active' && ANSWERING.includes(r.phase)) setOutcome('interrupted')
    r.status = 'abandoned'
    r.phase = 'summary'
    return
  }
  if (r.status === 'paused') {
    if (action !== 'resume') return
    const from = world.pausedFrom ?? r.phase
    r.status = 'active'
    if (ANSWERING.includes(from)) {
      r.phase = 'question'
      const fresh = attempt({
        id: world.nextAttemptId++,
        parent_attempt_id: r.current_attempt?.id ?? null,
        attempt_kind: r.current_attempt?.attempt_kind ?? 'initial',
      })
      r.current_attempt = fresh
      current.latest_attempt = fresh
    } else {
      r.phase = from
    }
    world.pausedFrom = null
    return
  }
  if (action === 'pause') {
    world.pausedFrom = r.phase
    r.status = 'paused'
    r.pause_count += 1
    if (ANSWERING.includes(r.phase)) setOutcome('interrupted')
    return
  }
  if (action === 'question_ready' && r.phase === 'question') r.phase = 'preparation'
  else if (action === 'skip' && (r.phase === 'question' || r.phase === 'preparation')) {
    setOutcome('skipped')
    afterAnswer()
  } else if (action === 'start_answer' && r.phase === 'preparation') {
    r.phase = 'recording'
    setOutcome('pending', { start_reason: body.start_reason ?? null })
  } else if (action === 'dont_know' && ANSWERING.includes(r.phase)) {
    setOutcome('dont_know')
    afterAnswer()
  } else if (action === 'mic_failure' && ANSWERING.includes(r.phase)) {
    setOutcome('technical_failure')
    afterAnswer()
  } else if (action === 'stop' && r.phase === 'recording') {
    setOutcome('answered', { processing_status: 'processing' })
    if (r.part === 'main') world.mainAttempt = r.current_attempt
    if (timing === 'practice' || (followUps && r.part === 'main')) r.phase = 'processing'
    else afterAnswer()
  } else if (action === 'next' && r.phase === 'feedback') advance()
  else if (action === 'answer_again' && r.phase === 'feedback' && r.can_answer_again) {
    const fresh = attempt({ id: world.nextAttemptId++, attempt_kind: 'immediate', parent_attempt_id: r.current_attempt?.id ?? null })
    world.repeated.add(r.current_index)
    r.current_attempt = fresh
    current.latest_attempt = fresh
    r.phase = 'question'
  }
  else if (action === 'no_follow_up' && r.phase === 'processing' && r.part === 'main') {
    if (r.follow_up) r.follow_up = { ...r.follow_up, status: 'omitted' }
    afterAnswer()
  }
}

export interface RoundWorldOptions {
  timing?: FeedbackTiming
  state?: Partial<RoundState>
  results?: AttemptResult[]
}

export function setupRoundWorld(options: RoundWorldOptions = {}): RoundWorld {
  const world: RoundWorld = {
    round: round(options.state, options.timing ?? 'practice'),
    summary: {
      round_id: 5,
      status: 'finished',
      provisional: false,
      outcomes: { answered: 2, dont_know: 1 },
      unresolved_attempt_ids: [],
      interrupted_attempt_ids: [],
      pause_count: 0,
      paused_ms: 0,
    },
    requests: [],
    results: options.results ?? [result()],
    retryResult: result({ stage: 'saved' }),
    createError: null,
    events: [],
    nextAttemptId: 11,
    pausedFrom: null,
    resultCalls: 0,
    followUps: [],
    retryFollowUps: ['generating', 'ready'],
    mainAttempt: null,
    roundReads: 0,
    offerAgain: false,
    repeated: new Set(),
    recoverAction: 'none',
  }
  let followUpIndex = 0
  /** A follow-up state read by GET /rounds while the round waits in processing for it. */
  const readFollowUp = () => {
    const r = world.round
    if (!(r.status === 'active' && r.phase === 'processing' && r.part === 'main' && r.follow_up)) return
    if (r.follow_up.status !== 'generating') return
    const status = world.followUps[Math.min(followUpIndex, world.followUps.length - 1)]
    followUpIndex += 1
    if (status === 'ready') {
      const main = r.current_attempt as AttemptOut
      world.mainAttempt = main
      const fresh = attempt({ id: world.nextAttemptId++, attempt_kind: 'followup', parent_attempt_id: main.id })
      r.part = 'followup'
      r.phase = 'question'
      r.current_attempt = fresh
      r.follow_up = followUpState('ready')
      r.current = { item_id: 90, question_version_id: 91, text: FOLLOW_UP_TEXT, style: 'short', difficulty: 'recall' }
    } else {
      r.follow_up = followUpState(status)
    }
  }
  const log = async (request: Request): Promise<unknown> => {
    const text = request.method === 'GET' ? '' : await request.clone().text()
    let body: unknown = text
    try {
      body = text ? JSON.parse(text) : undefined
    } catch {
      // multipart bodies stay text
    }
    world.requests.push({ method: request.method, path: new URL(request.url).pathname, body })
    return body
  }
  let resultIndex = 0
  server.use(
    http.post('/api/rounds', async ({ request }) => {
      await log(request)
      if (world.createError) {
        return HttpResponse.json({ detail: world.createError.detail }, { status: world.createError.status })
      }
      return HttpResponse.json(world.round, { status: 201 })
    }),
    http.get('/api/subjects/:id', () => HttpResponse.json({ id: 1, name: 'Demokracja ateńska' })),
    http.get('/api/rounds/:id', () => {
      world.roundReads += 1
      readFollowUp()
      return HttpResponse.json(world.round)
    }),
    http.post('/api/follow-ups/:id/retry', async ({ request, params }) => {
      await log(request)
      world.followUps = world.retryFollowUps
      followUpIndex = 0
      world.round.follow_up = followUpState('generating', { id: Number(params.id) })
      return HttpResponse.json(world.round.follow_up, { status: 202 })
    }),
    http.post('/api/rounds/:id/actions', async ({ request }) => {
      const body = (await log(request)) as ActionBody
      applyAction(world, body)
      return HttpResponse.json(world.round)
    }),
    http.get('/api/rounds/:id/summary', () => HttpResponse.json(world.summary)),
    http.post('/api/attempts/:id/events', async ({ request, params }) => {
      const body = (await log(request)) as AttemptEventsIn
      world.events.push({ attemptId: Number(params.id), body })
      return HttpResponse.json({ attempt_id: Number(params.id), stored: true })
    }),
    http.post('/api/attempts/:id/audio', async ({ request, params }) => {
      await log(request)
      return HttpResponse.json(
        { attempt_id: Number(params.id), recording_id: 31, duration_ms: 12000 },
        { status: 201 },
      )
    }),
    http.get('/api/attempts/:id/result', ({ params }) => {
      world.resultCalls += 1
      const next = world.results[Math.min(resultIndex, world.results.length - 1)]
      resultIndex += 1
      // like the server: a practice round moves on to feedback when the result arrives, or waits
      // for the follow-up of a main answer (the main result is withheld meanwhile)
      const r = world.round
      if (next.status !== 'processing' && r.status === 'active' && r.phase === 'processing') {
        const repeat = r.current_attempt?.attempt_kind === 'immediate'
        if (r.part === 'main' && !repeat && r.config.follow_ups?.enabled && world.followUps.length > 0 && next.status === 'ready') {
          if (!r.follow_up) r.follow_up = followUpState('generating')
          return HttpResponse.json({ ...next, attempt_id: Number(params.id), withheld: true, transcript: null, assessment: null })
        }
        if (r.part === 'followup') {
          r.part = 'main'
          if (world.mainAttempt) r.current_attempt = world.mainAttempt
          if (r.config.feedback_timing === 'practice') r.phase = 'feedback'
        } else if (r.config.feedback_timing === 'practice') {
          r.phase = 'feedback'
        } else {
          applyAction(world, { action: 'no_follow_up', from_phase: 'processing' })
        }
        syncAgain(world)
      }
      return HttpResponse.json({ ...next, attempt_id: Number(params.id) })
    }),
    http.post('/api/attempts/:id/recover', async ({ request, params }) => {
      await log(request)
      return HttpResponse.json({ attempt_id: Number(params.id), action: world.recoverAction, operation_id: null })
    }),
    http.post('/api/rounds/:id/recover', async ({ request, params }) => {
      await log(request)
      return HttpResponse.json({ round_id: Number(params.id), attempts: [] })
    }),
    http.post('/api/attempts/:id/retry', async ({ request, params }) => {
      await log(request)
      resultIndex = 0
      return HttpResponse.json({ ...world.retryResult, attempt_id: Number(params.id) }, { status: 202 })
    }),
    http.post('/api/attempts/:id/give-up', async ({ request, params }) => {
      await log(request)
      if (world.giveUpError) {
        return HttpResponse.json({ detail: world.giveUpError.detail }, { status: world.giveUpError.status })
      }
      // like the server: the answer is final, and a round waiting in processing for it moves on
      const id = Number(params.id)
      const r = world.round
      if (r.current_attempt?.id === id) {
        const givenUp = { ...r.current_attempt, outcome: 'given_up', processing_status: 'ready' }
        r.current_attempt = givenUp
        const item = r.items[r.current_index]
        if (item?.latest_attempt?.id === id) item.latest_attempt = givenUp
        if (world.giveUpSettles !== false && r.status === 'active' && r.phase === 'processing' && r.part === 'main') {
          if (r.config.feedback_timing === 'practice') r.phase = 'feedback'
          else applyAction(world, { action: 'no_follow_up', from_phase: 'processing' })
        }
      }
      world.results = [givenUpResult({ attempt_id: id })]
      resultIndex = 0
      syncAgain(world)
      // the server never offers "Answer again" after a give-up
      world.round.can_answer_again = false
      return HttpResponse.json(givenUpResult({ attempt_id: id }))
    }),
  )
  return world
}
