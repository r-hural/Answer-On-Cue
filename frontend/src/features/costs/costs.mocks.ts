import { http, HttpResponse } from 'msw'
import type {
  JobEstimate,
  MonthState,
  MonthSummary,
  PriceRow,
  RoundEstimate,
  UsageTable,
} from './api'

// No server import here: src/test/msw.ts takes costsHandlers as default handlers.

export const monthNoCeiling: MonthState = {
  month: '2026-10',
  ceiling_usd: 0,
  spent_usd: 2.4,
  reserved_usd: 0,
  remaining_usd: null,
}

export const monthWithCeiling: MonthState = {
  ...monthNoCeiling,
  ceiling_usd: 15,
  remaining_usd: 12.6,
}

export const summaryNoCeiling: MonthSummary = {
  month: monthNoCeiling,
  rounds: 6,
  per_round_usd: 0.31,
  library_usd: 0.54,
  voice_usd: 0,
  voice_provider: 'piper',
  blocked: 0,
  months: ['2026-10', '2026-09'],
}

export const summaryWithCeiling: MonthSummary = { ...summaryNoCeiling, month: monthWithCeiling }

export const emptyUsage: UsageTable = { month: '2026-10', group: 'operation', rows: [] }

export const roundEstimate: RoundEstimate = {
  low_usd: 0.45,
  high_usd: 0.55,
  transcription_minutes: 25,
  basis: 'typical',
  parts: [
    { operation: 'attempt.transcribe', usd: 0.14 },
    { operation: 'attempt.assess', usd: 0.32 },
    { operation: 'follow_up.generate', usd: 0.09 },
  ],
  voice_provider: 'piper',
  month: monthNoCeiling,
  fits: true,
}

export const generationEstimate: JobEstimate = {
  kind: 'questions.generate',
  usd: 0.3,
  questions: 42,
  sections: 0,
  minutes: 4,
  provider: 'openai',
  basis: 'typical',
  month: monthNoCeiling,
  fits: true,
}

export const extractionEstimate: JobEstimate = {
  ...generationEstimate,
  kind: 'topics.extract',
  usd: 0.04,
  questions: 0,
  sections: 3,
  minutes: 2,
}

export const prices: PriceRow[] = [
  { provider: 'openai', model: 'gpt-transcribe', role: 'transcription', input_per_m: 0,
    output_per_m: 0, audio_per_min: 0.0045, chars_per_m: 0, checked: '2026-10-20' },
  { provider: 'openai', model: 'gpt-6.1-sol', role: 'text', input_per_m: 2, output_per_m: 10,
    audio_per_min: 0, chars_per_m: 0, checked: '2026-10-20' },
]

/** Defaults for every test: no ceiling, nothing used yet in tables, estimates that fit. */
export const costsHandlers = [
  http.get('/api/costs/summary', () => HttpResponse.json(summaryNoCeiling)),
  http.get('/api/costs/usage', () => HttpResponse.json(emptyUsage)),
  http.get('/api/costs/estimate/round', () => HttpResponse.json(roundEstimate)),
  http.post('/api/costs/estimate/generation', () => HttpResponse.json(generationEstimate)),
  http.get('/api/costs/estimate/extraction', () => HttpResponse.json(extractionEstimate)),
  http.get('/api/costs/prices', () => HttpResponse.json(prices)),
]
