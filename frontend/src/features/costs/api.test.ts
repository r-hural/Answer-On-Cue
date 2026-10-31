import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import {
  fetchExtractionEstimate,
  fetchGenerationEstimate,
  fetchPrices,
  fetchRoundEstimate,
  fetchSummary,
  fetchUsage,
} from './api'

function capture() {
  const seen: { method: string; path: string; search: string; body: unknown }[] = []
  server.use(
    http.all('/api/costs/*', async ({ request }) => {
      const url = new URL(request.url)
      const text = request.method === 'GET' ? '' : await request.text()
      seen.push({
        method: request.method,
        path: url.pathname,
        search: url.search,
        body: text ? JSON.parse(text) : undefined,
      })
      return HttpResponse.json({})
    }),
  )
  return seen
}

describe('costs api', () => {
  it('sends every request to its path with its query or body', async () => {
    const seen = capture()
    await fetchSummary()
    await fetchSummary('2026-09')
    await fetchUsage('subject', '2026-10')
    await fetchUsage('operation')
    await fetchRoundEstimate({ subjectId: 3, questionCount: 10, answerSeconds: 120, followUps: true })
    await fetchGenerationEstimate({ subject_id: 3, per_topic: 5, topic_ids: [1, 2] })
    await fetchExtractionEstimate(7)
    await fetchPrices()
    expect(seen).toEqual([
      { method: 'GET', path: '/api/costs/summary', search: '', body: undefined },
      { method: 'GET', path: '/api/costs/summary', search: '?month=2026-09', body: undefined },
      { method: 'GET', path: '/api/costs/usage', search: '?group=subject&month=2026-10', body: undefined },
      { method: 'GET', path: '/api/costs/usage', search: '?group=operation', body: undefined },
      {
        method: 'GET',
        path: '/api/costs/estimate/round',
        search: '?subject_id=3&question_count=10&answer_seconds=120&follow_ups=true',
        body: undefined,
      },
      {
        method: 'POST',
        path: '/api/costs/estimate/generation',
        search: '',
        body: { subject_id: 3, per_topic: 5, topic_ids: [1, 2] },
      },
      {
        method: 'GET',
        path: '/api/costs/estimate/extraction',
        search: '?document_version_id=7',
        body: undefined,
      },
      { method: 'GET', path: '/api/costs/prices', search: '', body: undefined },
    ])
  })
})
