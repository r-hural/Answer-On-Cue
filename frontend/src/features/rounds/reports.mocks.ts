import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import { FOLLOW_UP_TEXT, config } from './rounds.mocks'
import type {
  AiAssessment,
  AnswerMetrics,
  AttemptReport,
  FollowUpBrief,
  FollowUpReason,
  FollowUpSeries,
  FullAssessment,
  HistoryRow,
  ItemRow,
  RegisterKey,
  ReportResult,
  RoundDeliverySummary,
  RoundKnowledgeSummary,
  RoundReport,
  TranscriptReportOut,
} from './reports'

export const QUESTION_TEXT =
  'Wyjaśnij, na czym polegała procedura ostracyzmu w Atenach i jaki był jej cel polityczny.'
export const TRANSCRIPT =
  'No więc ostracyzm to było takie głosowanie, jakby, w Atenach, gdzie ludzie pisali na skorupach imię kogoś, kogo chcieli wygnać z miasta. Eee… to było, wydaje mi się, na dziesięć lat.'

const REGISTER_TEXT: Record<RegisterKey, string> = {
  grammar: 'Gramatyka poprawna.',
  vocabulary: 'Słownictwo: „ta rzecz się powtarzała” → „zagrożenie tyranią powracało”.',
  formality: 'Potoczne wtrącenia: „no więc”, „jakby” (3×).',
  terminology: '„Ostrakon” użyty poprawnie; „ta rzecz” zamiast nazwy zjawiska.',
  structure: 'Brak wyraźnego otwarcia; cel podany na końcu.',
  precision: '„Wydaje mi się” osłabia poprawną informację.',
}

export function ai(patch: Partial<AiAssessment> = {}): AiAssessment {
  return {
    correctness: 70,
    correctness_evidence: ['na dziesięć lat'],
    points: [
      { point_id: 'p1', status: 'covered', evidence: 'pisali na skorupach', note: 'Głosowanie na skorupach.' },
      { point_id: 'p2', status: 'partial', evidence: 'na dziesięć lat', note: 'Podane niepewnie.' },
      { point_id: 'p3', status: 'missing', evidence: null, note: 'Nie wspomniano o kworum.' },
    ],
    factual_mistakes: [],
    enrichment_mentioned: [],
    outside_material: [],
    language: 66,
    language_register: {
      grammar: { summary: REGISTER_TEXT.grammar, quotes: [] },
      vocabulary: { summary: REGISTER_TEXT.vocabulary, quotes: [] },
      formality: { summary: REGISTER_TEXT.formality, quotes: [] },
      terminology: { summary: REGISTER_TEXT.terminology, quotes: [] },
      structure: { summary: REGISTER_TEXT.structure, quotes: [] },
      precision: { summary: REGISTER_TEXT.precision, quotes: [] },
    },
    language_issues: [
      { dimension: 'vocabulary', quote: 'ta rzecz się powtarzała', suggestion: 'zagrożenie tyranią powracało', explanation: 'Nazwij zjawisko.' },
    ],
    better_version: 'Ostracyzm był procedurą, w której obywatele Aten zapisywali na skorupach imię osoby uznanej za zagrożenie.',
    improvements: ['Zacznij od zdania definiującego ostracyzm.'],
    fluency: 62,
    fluency_coverage: 'full',
    fluency_confidence: 'medium',
    fluency_feedback: 'Kilka dłuższych przerw i wypełniaczy.',
    hesitation_examples: ['eee'],
    transcription_doubts: [],
    uncertainty_flags: [],
    ...patch,
  }
}

export function metrics(patch: Partial<AnswerMetrics> = {}): AnswerMetrics {
  return {
    preparation_used_ms: 8200,
    speech_start_latency_ms: 0,
    total_response_latency_ms: 8200,
    answer_duration_ms: 112_000,
    first_to_last_speech_ms: 107_000,
    speech_ms: 90_000,
    leading_silence_ms: 300,
    trailing_silence_ms: 5000,
    internal_silence_ms: 19_000,
    silence_ratio: 0.18,
    longest_internal_silence_ms: 4100,
    long_pause_count: 3,
    very_long_pause_count: 0,
    long_pauses_per_min: 1.7,
    words: 210,
    words_per_minute: 118,
    filler_count: 5,
    filler_per_min: 2.8,
    filler_examples: ['eee', 'jakby'],
    fillers_lower_bound: true,
    pauses: [
      { start_ms: 41_000, end_ms: 45_100, kind: 'long' },
      { start_ms: 65_000, end_ms: 67_300, kind: 'long' },
      { start_ms: 90_000, end_ms: 92_000, kind: 'long' },
    ],
    flags: [],
    unavailable: {},
    approximate: true,
    interrupted: false,
    detector: 'webrtcvad-2/30ms',
    ...patch,
  }
}

export function assessment(patch: Partial<FullAssessment> = {}): FullAssessment {
  return {
    status: 'assessed',
    scores: { correctness: 70, completeness: 40, factual: 51, language: 66, total: 54 },
    band: 'needs_improvement',
    model_answer_display: 'on_request',
    ai: ai(),
    band_bounds: [30, 60, 80],
    factual_weights: [30, 50],
    ...patch,
  }
}

export function reportResult(patch: Partial<ReportResult> = {}): ReportResult {
  return {
    attempt_id: 10,
    outcome: 'answered',
    status: 'ready',
    stage: 'done',
    recording: { id: 31, duration_ms: 112_000 },
    failure: null,
    withheld: false,
    transcript: TRANSCRIPT,
    metrics: metrics(),
    assessment: assessment(),
    ...patch,
  }
}

const MODEL_ANSWER =
  'Ostracyzm był procedurą, w której zgromadzenie ludowe raz w roku decydowało, czy głosować nad wygnaniem obywatela uznanego za zagrożenie dla demokracji.'

export function attemptReport(patch: Partial<AttemptReport> = {}): AttemptReport {
  return {
    attempt_id: 10,
    round_id: 5,
    subject_id: 1,
    subject_name: 'Demokracja ateńska',
    ordinal: 3,
    total: 10,
    feedback_timing: 'practice',
    round_status: 'active',
    question_text: QUESTION_TEXT,
    result: reportResult(),
    points: [
      { id: 'p1', text: 'Głosowanie na skorupach (ostrakonach) i wskazywanie imienia osoby do wygnania.', status: 'covered', evidence: 'pisali na skorupach', note: 'Głosowanie na skorupach.' },
      { id: 'p2', text: 'Wygnanie na dziesięć lat.', status: 'partial', evidence: 'na dziesięć lat', note: 'Podane niepewnie.' },
      { id: 'p3', text: 'Kworum 6000 głosów.', status: 'missing', evidence: null, note: 'Nie wspomniano o kworum.' },
    ],
    key_points: [],
    model_answer: MODEL_ANSWER,
    expanded_explanation: 'Szersze wyjaśnienie ostracyzmu i jego skutków politycznych.',
    model_answer_display: 'on_request',
    sources: [
      { passage_id: 1, document_title: 'Demokracja_atenska_rozdzialy_1-4.pdf', locator: 'p. 14 ¶1', heading: 'Ostracyzm', excerpt: 'Raz w roku zgromadzenie decydowało, czy przeprowadzić ostracyzm…' },
      { passage_id: 2, document_title: 'Demokracja_atenska_rozdzialy_1-4.pdf', locator: 'p. 14 ¶2', heading: null, excerpt: 'Do ważności głosowania potrzeba było co najmniej 6000 głosów…' },
    ],
    transcript_report: null,
    ...patch,
  }
}

/** An answer to an expanded question: one point beyond the materials, one partly. */
export function expandedReport(patch: Partial<AttemptReport> = {}): AttemptReport {
  const base = attemptReport()
  return attemptReport({
    source_policy: 'expanded',
    question_basis: 'mixed',
    points: [
      { ...base.points[0], basis: 'source', disputed: false },
      { ...base.points[1], basis: 'mixed', disputed: false },
      { ...base.points[2], basis: 'expanded', disputed: false },
    ],
    beyond_material: ['Ostatni znany ostracyzm: Hyperbolos około 417 roku p.n.e.'],
    source_conflicts: [],
    ...patch,
  })
}

/** An answer whose sources disagree on the missing point p3: it is not graded. */
export function conflictReport(patch: Partial<AttemptReport> = {}): AttemptReport {
  const base = attemptReport()
  return attemptReport({
    points: [base.points[0], base.points[1], { ...base.points[2], disputed: true }],
    source_conflicts: [
      {
        point_id: 'p3',
        point_text: base.points[2].text,
        note: 'Fragmenty podają różną liczbę głosów potrzebną do ważności.',
        sources: [
          { passage_id: 2, document_title: 'Demokracja_atenska_rozdzialy_1-4.pdf', locator: 'p. 14 ¶2', heading: null, excerpt: 'Do ważności głosowania potrzeba było co najmniej 6000 głosów…' },
          { passage_id: 3, document_title: 'Demokracja_atenska_rozdzialy_1-4.pdf', locator: 'p. 15 ¶1', heading: null, excerpt: 'Wystarczyła zwykła większość głosujących…' },
        ],
      },
    ],
    ...patch,
  })
}

/** An assessed answer in the Weak band: the model answer opens by itself. */
export function weakReport(): AttemptReport {
  return attemptReport({
    result: reportResult({
      assessment: assessment({
        band: 'weak',
        model_answer_display: 'auto',
        scores: { correctness: 20, completeness: 10, factual: 13, language: 50, total: 20 },
      }),
    }),
    model_answer_display: 'auto',
    key_points: ['Głosowanie na skorupach.', 'Wygnanie na dziesięć lat.', 'Kworum 6000 głosów.'],
  })
}

export function goodReport(): AttemptReport {
  return attemptReport({
    result: reportResult({
      assessment: assessment({
        band: 'good',
        scores: { correctness: 80, completeness: 70, factual: 74, language: 80, total: 75 },
      }),
    }),
  })
}

export function dontKnowReport(): AttemptReport {
  return attemptReport({
    result: reportResult({
      outcome: 'dont_know',
      recording: null,
      transcript: null,
      metrics: null,
      assessment: assessment({
        status: 'no_answer',
        band: 'weak',
        model_answer_display: 'auto',
        ai: null,
        scores: { correctness: 0, completeness: 0, factual: 0, language: null, total: 0 },
      }),
    }),
    points: [],
    model_answer_display: 'auto',
    key_points: ['Głosowanie na skorupach.', 'Wygnanie na dziesięć lat.'],
  })
}

export function noSpeechReport(): AttemptReport {
  return attemptReport({
    result: reportResult({
      transcript: null,
      metrics: metrics({
        speech_start_latency_ms: null,
        total_response_latency_ms: null,
        first_to_last_speech_ms: null,
        speech_ms: 0,
        pauses: [],
        long_pause_count: null,
        words: null,
        words_per_minute: null,
        filler_count: null,
        unavailable: { speech_start_latency_ms: 'no_speech', words_per_minute: 'no_speech' },
      }),
      assessment: assessment({ status: 'no_speech', band: null, scores: null, model_answer_display: null, ai: null }),
    }),
    points: [],
    model_answer: null,
    expanded_explanation: null,
    model_answer_display: null,
    sources: [],
  })
}

/** A technical failure: never a score, no points, no model answer. */
export function failedReport(kind = 'timeout', retryable = true): AttemptReport {
  const message =
    kind === 'audio_missing'
      ? 'The recording of this answer never reached the app, so it was not assessed.'
      : 'The transcription service did not respond.'
  return attemptReport({
    result: reportResult({
      outcome: 'technical_failure',
      status: 'failed',
      stage: kind === 'audio_missing' ? 'waiting_for_audio' : 'transcribing',
      recording: kind === 'audio_missing' ? null : { id: 31, duration_ms: 112_000 },
      failure: { kind, message, retryable },
      transcript: null,
      metrics: null,
      assessment: null,
    }),
    points: [],
    key_points: [],
    model_answer: null,
    expanded_explanation: null,
    model_answer_display: null,
    sources: [],
  })
}

/** A failed answer that was given up: ready, not assessed, the recording kept. */
export function givenUpReport(patch: Partial<AttemptReport> = {}): AttemptReport {
  const base = failedReport()
  return attemptReport({
    ...base,
    round_status: 'finished',
    result: reportResult({
      ...base.result,
      outcome: 'given_up',
      status: 'ready',
      stage: 'not_assessed',
      failure: null,
    }),
    ...patch,
  })
}

export function item(patch: Partial<ItemRow> = {}): ItemRow {
  return {
    ordinal: 1,
    item_id: 50,
    attempt_id: 10,
    question_text: 'Jaki był cel sejsachtei wprowadzonej przez Solona?',
    outcome: 'answered',
    status: 'ready',
    band: 'very_good',
    factual: 88,
    total: 90,
    preparation_used_ms: 4000,
    speech_start_latency_ms: 300,
    auto_start: false,
    fluency: 81,
    restarted: false,
    failure: null,
    ...patch,
  }
}

export function knowledge(patch: Partial<RoundKnowledgeSummary> = {}): RoundKnowledgeSummary {
  return {
    primary_score: 64,
    mean_correctness: 71,
    mean_completeness: 58,
    mean_factual: 63,
    mean_language: 69,
    language_assessable: 8,
    mean_fluency: 66,
    fluency_assessable: 7,
    band_distribution: { weak: 2, needs_improvement: 2, good: 5, very_good: 1 },
    assessed: 9,
    no_answer: 1,
    no_speech: 0,
    unresolved: 0,
    provisional: false,
    ...patch,
  }
}

export function delivery(patch: Partial<RoundDeliverySummary> = {}): RoundDeliverySummary {
  return {
    count_included: 9,
    count_excluded_interrupted: 0,
    count_excluded_other_kind: 0,
    median_preparation_used_ms: 11_000,
    median_speech_start_latency_ms: 800,
    median_total_response_latency_ms: 12_000,
    median_total_response_latency_successful_ms: 14_000,
    target_attainment: { speech_start_latency_ms: [6, 8] },
    median_longest_pause_ms: 3200,
    long_pauses_per_speaking_min: 1.4,
    aggregate_silence_ratio: 0.16,
    median_words_per_minute: 120,
    filler_per_min: 2.5,
    ...patch,
  }
}

/** A finished 4-question round of Polish questions; every item is resolved. */
export function roundReport(patch: Partial<RoundReport> = {}): RoundReport {
  return {
    round_id: 5,
    subject_id: 1,
    subject_name: 'Demokracja ateńska',
    status: 'finished',
    started_at: '2026-10-22T16:05:00Z',
    finished_at: '2026-10-22T16:41:00Z',
    config: config({ question_count: 4 }),
    withheld: false,
    provisional: false,
    asked: 4,
    total: 4,
    knowledge: knowledge({ band_distribution: { weak: 1, needs_improvement: 1, good: 1, very_good: 1 } }),
    delivery: delivery(),
    items: [
      item(),
      item({
        ordinal: 2,
        item_id: 51,
        attempt_id: 11,
        question_text: 'Omów reformy Klejstenesa i wyjaśnij, w jaki sposób podział obywateli na dziesięć fyl osłabił wpływy rodów.',
        band: 'needs_improvement',
        factual: 44,
        total: 50,
        preparation_used_ms: 21_000,
        speech_start_latency_ms: 3800,
        fluency: 58,
      }),
      item({
        ordinal: 3,
        item_id: 52,
        attempt_id: 12,
        question_text: 'Porównaj kompetencje zgromadzenia ludowego (eklezji) i Rady Pięciuset (bule).',
        band: 'good',
        factual: 68,
        total: 70,
        preparation_used_ms: 27_000,
        speech_start_latency_ms: 1100,
        fluency: 70,
        restarted: true,
      }),
      item({
        ordinal: 4,
        item_id: 53,
        attempt_id: 13,
        question_text: 'Kim byli metojkowie i jakie mieli prawa?',
        outcome: 'dont_know',
        band: 'weak',
        factual: 0,
        total: 0,
        preparation_used_ms: null,
        speech_start_latency_ms: null,
        fluency: null,
      }),
    ],
    pause_count: 1,
    paused_ms: 240_000,
    restarted_ordinals: [3],
    ...patch,
  }
}

/** The same round with one answer that could not be processed. */
export function provisionalRoundReport(): RoundReport {
  const base = roundReport()
  return roundReport({
    provisional: true,
    knowledge: knowledge({
      primary_score: 61,
      band_distribution: { weak: 0, needs_improvement: 2, good: 1, very_good: 0 },
      unresolved: 1,
      provisional: true,
      language_assessable: 3,
      fluency_assessable: 3,
      mean_fluency: 64,
    }),
    items: [
      base.items[0],
      base.items[1],
      item({
        ordinal: 3,
        item_id: 52,
        attempt_id: 12,
        question_text: 'Wyjaśnij, na czym polegała procedura ostracyzmu…',
        status: 'failed',
        band: null,
        factual: null,
        total: null,
        preparation_used_ms: null,
        speech_start_latency_ms: null,
        fluency: null,
        outcome: 'technical_failure',
        failure: { kind: 'timeout', message: 'The transcription service did not respond.', retryable: true },
      }),
      base.items[3],
    ],
    restarted_ordinals: [],
  })
}

export function historyRow(patch: Partial<HistoryRow> = {}): HistoryRow {
  return {
    round_id: 5,
    subject_id: 1,
    subject_name: 'Demokracja ateńska',
    status: 'finished',
    started_at: '2026-10-22T16:05:00Z',
    finished_at: '2026-10-22T16:41:00Z',
    feedback_timing: 'practice',
    total: 10,
    asked: 10,
    primary_score: 64,
    provisional: false,
    band_distribution: { weak: 2, needs_improvement: 2, good: 5, very_good: 1 },
    median_preparation_used_ms: 11_000,
    mean_fluency: 66,
    withheld: false,
    ...patch,
  }
}

/** An asked, assessed follow-up as the main answer's report shows it. */
export function askedBrief(patch: Partial<FollowUpBrief> = {}): FollowUpBrief {
  return {
    asked: true,
    reason: null,
    failure_message: null,
    question_text: FOLLOW_UP_TEXT,
    attempt_id: 21,
    outcome: 'answered',
    status: 'ready',
    band: 'good',
    factual: 72.4,
    note: 'Podano przykład Arystydesa. Uzasadnienie zagrożenia było ogólnikowe.',
    ...patch,
  }
}

export function notAskedBrief(reason: FollowUpReason, failureMessage: string | null = null): FollowUpBrief {
  return {
    asked: false,
    reason,
    failure_message: failureMessage,
    question_text: null,
    attempt_id: null,
    outcome: null,
    status: null,
    band: null,
    factual: null,
    note: null,
  }
}

/** The report of a follow-up answer (its own scores, no follow-up box). */
export function followUpReport(patch: Partial<AttemptReport> = {}): AttemptReport {
  return attemptReport({
    attempt_id: 21,
    question_text: FOLLOW_UP_TEXT,
    role: 'followup',
    parent_attempt_id: 10,
    follow_up: null,
    result: reportResult({
      attempt_id: 21,
      assessment: assessment({
        band: 'good',
        scores: { correctness: 80, completeness: 70, factual: 72.4, language: 80, total: 74 },
      }),
    }),
    ...patch,
  })
}

export function followUpSeries(patch: Partial<FollowUpSeries> = {}): FollowUpSeries {
  return {
    asked: 1,
    not_asked: 0,
    unresolved: 0,
    good_or_better: 1,
    provisional: false,
    processing: 0,
    knowledge: knowledge({
      primary_score: 91,
      mean_factual: 89,
      band_distribution: { weak: 0, needs_improvement: 0, good: 0, very_good: 1 },
      assessed: 1,
    }),
    ...patch,
  }
}

/** A finished round with follow-ups on: the follow-ups score higher than the main answers. */
export function roundReportWithFollowUps(patch: Partial<RoundReport> = {}): RoundReport {
  return roundReport({
    config: config({ question_count: 4, follow_ups: { enabled: true, prep_seconds: 15, answer_seconds: 60 } }),
    follow_ups: followUpSeries(),
    ...patch,
  })
}

export interface ReportsWorld {
  /** Reports by attempt id; GET answers 404 for an id not listed. */
  attempts: Record<number, AttemptReport>
  /** Successive answers of GET /reports/rounds/:id; the last one repeats. */
  rounds: RoundReport[]
  history: HistoryRow[]
  /** Every request to /api/reports/history, as its query string. */
  historyQueries: string[]
  roundCalls: number
  puts: { attemptId: number; body: { corrected_text: string } }[]
  /** The answer of PUT transcript-report; set `putError` to fail it. */
  putError: { status: number; detail: string } | null
  /** Attempt ids POSTed to /attempts/:id/retry (only when the world was set up with 
etry: true). */
  retries: number[]
  /** Runs when a retry is accepted: change ttempts here to what the server would show next. */
  onRetry: ((attemptId: number) => void) | null
  /** POST /rounds/:id/recover calls, and how many report reads came before the first. */
  recoverCalls: number
  roundCallsAtRecover: number | null
}

export interface ReportsWorldOptions {
  attempts?: AttemptReport[]
  rounds?: RoundReport[]
  history?: HistoryRow[]
  /** Also answer POST /attempts/:id/retry; off by default so a round world's own handler stays in charge. */
  retry?: boolean
}

export function setupReportsWorld(options: ReportsWorldOptions = {}): ReportsWorld {
  const world: ReportsWorld = {
    attempts: Object.fromEntries((options.attempts ?? [attemptReport()]).map((report) => [report.attempt_id, report])),
    rounds: options.rounds ?? [roundReport()],
    history: options.history ?? [],
    historyQueries: [],
    roundCalls: 0,
    puts: [],
    putError: null,
    retries: [],
    onRetry: null,
    recoverCalls: 0,
    roundCallsAtRecover: null,
  }
  server.use(
    http.get('/api/reports/attempts/:id', ({ params }) => {
      const report = world.attempts[Number(params.id)]
      if (!report) return HttpResponse.json({ detail: 'Attempt not found.' }, { status: 404 })
      return HttpResponse.json(report)
    }),
    http.put('/api/reports/attempts/:id/transcript-report', async ({ request, params }) => {
      const body = (await request.json()) as { corrected_text: string }
      world.puts.push({ attemptId: Number(params.id), body })
      if (world.putError) return HttpResponse.json({ detail: world.putError.detail }, { status: world.putError.status })
      const out: TranscriptReportOut = {
        attempt_id: Number(params.id),
        corrected_text: body.corrected_text,
        created_at: '2026-10-22T16:30:00Z',
      }
      const report = world.attempts[Number(params.id)]
      if (report) report.transcript_report = out
      return HttpResponse.json(out)
    }),
    http.post('/api/rounds/:id/recover', ({ params }) => {
      world.recoverCalls += 1
      world.roundCallsAtRecover ??= world.roundCalls
      return HttpResponse.json({ round_id: Number(params.id), attempts: [] })
    }),
    http.get('/api/reports/rounds/:id', () => {
      const next = world.rounds[Math.min(world.roundCalls, world.rounds.length - 1)]
      world.roundCalls += 1
      return HttpResponse.json(next)
    }),
    http.get('/api/reports/history', ({ request }) => {
      const url = new URL(request.url)
      world.historyQueries.push(url.search)
      const subject = url.searchParams.get('subject_id')
      const timing = url.searchParams.get('feedback_timing')
      const rows = world.history.filter(
        (row) => (!subject || row.subject_id === Number(subject)) && (!timing || row.feedback_timing === timing),
      )
      return HttpResponse.json(rows)
    }),
  )
  if (options.retry) {
    server.use(
      http.post('/api/attempts/:id/retry', ({ params }) => {
        world.retries.push(Number(params.id))
        world.onRetry?.(Number(params.id))
        return HttpResponse.json(
          { ...reportResult({ status: 'processing', stage: 'saved', metrics: null, assessment: null, transcript: null }), attempt_id: Number(params.id) },
          { status: 202 },
        )
      }),
    )
  }
  return world
}
