import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import type { TrendPoint, TrendsOut } from './trends'

export function trendPoint(patch: Partial<TrendPoint> = {}): TrendPoint {
  return {
    round_id: 11,
    started_at: '2026-10-18T12:00:00Z',
    status: 'finished',
    round_type: 'standard',
    feedback_timing: 'practice',
    answers: 10,
    unresolved: 0,
    assisted: 0,
    restarted: 0,
    provisional: false,
    primary: 48,
    factual: 45,
    language: 58,
    language_assessable: 10,
    speed_answers: 10,
    median_preparation_used_ms: 17000,
    median_speech_start_latency_ms: 2400,
    median_total_response_latency_ms: 19400,
    median_total_response_latency_successful_ms: 18000,
    long_pauses_per_min: 2.6,
    silence_ratio: 0.22,
    filler_per_min: 2.1,
    filler_reliable: 8,
    fluency: 58,
    fluency_assessable: 8,
    band_bounds: [30, 60, 80],
    changes: [],
    ...patch,
  }
}

const DAYS = ['2026-10-12', '2026-10-14', '2026-10-15', '2026-10-17', '2026-10-20', '2026-10-21', '2026-10-22']
const PRIMARY = [48, 52, 55, 58, 57, 61, 64]
const FACTUAL = [45, 49, 53, 57, 55, 60, 63]
const LANGUAGE = [58, 60, 61, 62, 64, 66, 69]
const TOTAL = [19400, 18100, 17800, 15500, 10600, 9200, 11800]
const PREP = [17000, 16000, 16000, 14000, 9000, 8000, 11000]
const START = [2400, 2100, 1800, 1500, 1600, 1200, 800]
const PAUSES = [2.6, 2.4, 2.2, 1.9, 2.0, 1.6, 1.4]
const FLUENCY = [58, 60, 59, 63, 62, 64, 66]

/** The seven rounds of the template, 12 to 22 October. */
export function templatePoints(): TrendPoint[] {
  return DAYS.map((day, i) =>
    trendPoint({
      round_id: 100 + i,
      started_at: `${day}T12:00:00Z`,
      primary: PRIMARY[i],
      factual: FACTUAL[i],
      language: LANGUAGE[i],
      median_total_response_latency_ms: TOTAL[i],
      median_preparation_used_ms: PREP[i],
      median_speech_start_latency_ms: START[i],
      long_pauses_per_min: PAUSES[i],
      fluency: FLUENCY[i],
      silence_ratio: i === 0 ? 0.22 : 0.16,
      filler_reliable: i < 5 ? 1 : 0,
      filler_per_min: i < 5 ? 2.1 : null,
    }),
  )
}

export function trendsOut(patch: Partial<TrendsOut> = {}): TrendsOut {
  return {
    subject_id: 3,
    subject_name: 'Demokracja ateńska',
    filters: {
      subject_id: 3,
      source_policy: 'materials',
      series: 'main',
      style: null,
      difficulty: null,
      include_interrupted: false,
      include_assisted: false,
    },
    band_bounds: [30, 60, 80],
    points: templatePoints(),
    ...patch,
  }
}

export interface TrendsWorld {
  subjects: { id: number; name: string }[]
  /** The answer to one request; a number is an HTTP error status. */
  respond: (query: URLSearchParams) => TrendsOut | number
  /** Every requested URL (path and query), in order. */
  requests: string[]
}

/** MSW handlers for the subject list and the trends endpoint. */
export function setupTrendsWorld(patch: Partial<Omit<TrendsWorld, 'requests'>> = {}): TrendsWorld {
  const world: TrendsWorld = {
    subjects: [
      { id: 3, name: 'Demokracja ateńska' },
      { id: 4, name: 'Biologia komórki' },
    ],
    respond: (query) => trendsOut({ subject_id: Number(query.get('subject_id')) }),
    requests: [],
    ...patch,
  }
  server.use(
    http.get('/api/subjects', ({ request }) => {
      const url = new URL(request.url)
      world.requests.push(url.pathname + url.search)
      return HttpResponse.json(world.subjects.map((s) => ({ ...s, description: null, document_count: 1 })))
    }),
    http.get('/api/trends', ({ request }) => {
      const url = new URL(request.url)
      world.requests.push(url.pathname + url.search)
      const answer = world.respond(url.searchParams)
      return typeof answer === 'number'
        ? HttpResponse.json({ detail: 'Trends failed.' }, { status: answer })
        : HttpResponse.json(answer)
    }),
  )
  return world
}
