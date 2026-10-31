import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { ApiError } from '../../api/client'
import { getAttemptReport, getHistory, getRoundReport, putTranscriptReport } from './reports'
import {
  dontKnowReport,
  failedReport,
  historyRow,
  noSpeechReport,
  provisionalRoundReport,
  roundReport,
  setupReportsWorld,
  weakReport,
  attemptReport,
} from './reports.mocks'

describe('reports client', () => {
  it('reads an attempt report from /reports/attempts/:id', async () => {
    setupReportsWorld({ attempts: [attemptReport(), weakReport()].map((r, i) => ({ ...r, attempt_id: 10 + i })) })
    const report = await getAttemptReport(10)
    expect(report.result.assessment?.band).toBe('needs_improvement')
    expect(report.points.map((p) => p.status)).toEqual(['covered', 'partial', 'missing'])
    expect((await getAttemptReport(11)).result.assessment?.band).toBe('weak')
  })

  it('carries every kind of answer the feedback screen shows', async () => {
    setupReportsWorld({
      attempts: [
        { ...dontKnowReport(), attempt_id: 1 },
        { ...noSpeechReport(), attempt_id: 2 },
        { ...failedReport('audio_missing', false), attempt_id: 3 },
      ],
    })
    expect((await getAttemptReport(1)).result.assessment?.status).toBe('no_answer')
    expect((await getAttemptReport(2)).result.assessment?.band).toBeNull()
    const failed = await getAttemptReport(3)
    expect(failed.result.failure?.kind).toBe('audio_missing')
    expect(failed.result.assessment).toBeNull()
  })

  it('a missing attempt is an ApiError with the server message', async () => {
    setupReportsWorld()
    await expect(getAttemptReport(999)).rejects.toMatchObject({ status: 404, message: 'Attempt not found.' })
    await expect(getAttemptReport(999)).rejects.toBeInstanceOf(ApiError)
  })

  it('sends the corrected text with PUT', async () => {
    const world = setupReportsWorld()
    const out = await putTranscriptReport(10, 'na ostrakonach')
    expect(world.puts).toEqual([{ attemptId: 10, body: { corrected_text: 'na ostrakonach' } }])
    expect(out.corrected_text).toBe('na ostrakonach')
  })

  it('reads a round report from /reports/rounds/:id', async () => {
    setupReportsWorld({ rounds: [provisionalRoundReport(), roundReport()] })
    const first = await getRoundReport(5)
    expect(first.provisional).toBe(true)
    expect(first.items[2].status).toBe('failed')
    expect((await getRoundReport(5)).provisional).toBe(false)
  })

  it('history: no query without a filter, subject_id and feedback_timing with one', async () => {
    const world = setupReportsWorld({
      history: [historyRow(), historyRow({ round_id: 6, subject_id: 2, feedback_timing: 'exam' })],
    })
    expect(await getHistory()).toHaveLength(2)
    expect((await getHistory({ subjectId: 2 })).map((row) => row.round_id)).toEqual([6])
    expect((await getHistory({ feedbackTiming: 'practice' })).map((row) => row.round_id)).toEqual([5])
    expect(await getHistory({ subjectId: null, feedbackTiming: null })).toHaveLength(2)
    expect(world.historyQueries).toEqual(['', '?subject_id=2', '?feedback_timing=practice', ''])
  })

  it('uses the PUT method and the guard header for the correction', async () => {
    let seen: { method: string; guard: string | null } | null = null
    server.use(
      http.put('/api/reports/attempts/:id/transcript-report', ({ request }) => {
        seen = { method: request.method, guard: request.headers.get('X-AnswerOnCue') }
        return HttpResponse.json({ attempt_id: 1, corrected_text: 'x', created_at: '2026-10-22T16:30:00Z' })
      }),
    )
    await putTranscriptReport(1, 'x')
    expect(seen).toEqual({ method: 'PUT', guard: '1' })
  })
})
