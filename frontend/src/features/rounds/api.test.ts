import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import {
  act,
  createRound,
  getResult,
  getRound,
  postEvents,
  retryAttempt,
  retryFollowUp,
  roundSummary,
  uploadAnswer,
} from './api'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import { QUESTIONS, config, followUpState, setupRoundWorld } from './rounds.mocks'

const EVENTS = {
  question_shown_at: 1200,
  question_ready_at: 1200,
  playback_ms: 0,
  prep_end_at: 9400,
  start_reason: 'auto' as const,
  capture_start_at: 9400,
  stopped_at: 21400,
  pre_roll_ms: 1000,
  interrupted: false,
  listening_available: true,
}

describe('rounds api', () => {
  it('creates a round and reads it back', async () => {
    const world = setupRoundWorld()
    const body = { subject_id: 1, config: config() }
    const created = await createRound(body)
    expect(created.id).toBe(5)
    expect(world.requests[0]).toMatchObject({ method: 'POST', path: '/api/rounds', body })
    expect((await getRound(5)).current?.text).toBe(QUESTIONS[0])
  })

  it('exposes the available count of a 409 through ApiError.detail', async () => {
    const world = setupRoundWorld()
    world.createError = { status: 409, detail: { message: 'Only 2 questions match.', available: 2 } }
    const error = await createRound({ subject_id: 1, config: config() }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).message).toBe('Only 2 questions match.')
    expect((error as ApiError).detail).toMatchObject({ available: 2 })
  })

  it('sends an action with its attempt id', async () => {
    const world = setupRoundWorld()
    const next = await act(5, { action: 'question_ready', from_phase: 'question', attempt_id: 10 })
    expect(next.phase).toBe('preparation')
    expect(world.requests[0]).toMatchObject({
      method: 'POST',
      path: '/api/rounds/5/actions',
      body: { action: 'question_ready', from_phase: 'question', attempt_id: 10 },
    })
  })

  it('reads the round summary', async () => {
    setupRoundWorld()
    expect((await roundSummary(5)).outcomes).toEqual({ answered: 2, dont_know: 1 })
  })

  it('posts events as JSON', async () => {
    const world = setupRoundWorld()
    expect(await postEvents(7, EVENTS)).toEqual({ attempt_id: 7, stored: true })
    expect(world.requests[0]).toMatchObject({ method: 'POST', path: '/api/attempts/7/events', body: EVENTS })
  })

  it('uploads the answer as multipart field "file"', async () => {
    const world = setupRoundWorld()
    const out = await uploadAnswer(7, new Blob(['RIFF'], { type: 'audio/wav' }))
    expect(out).toEqual({ attempt_id: 7, recording_id: 31, duration_ms: 12000 })
    expect(world.requests[0].path).toBe('/api/attempts/7/audio')
    // jsdom's Blob does not survive undici's FormData, so the file part is checked only by name.
    expect(String(world.requests[0].body)).toMatch(/name="file"/)
  })

  it('retries a follow-up and continues without one', async () => {
    const world = setupRoundWorld()
    server.use(
      http.post('/api/follow-ups/:id/retry', async ({ request, params }) => {
        world.requests.push({ method: request.method, path: new URL(request.url).pathname, body: undefined })
        return HttpResponse.json(followUpState('generating', { id: Number(params.id) }), { status: 202 })
      }),
    )
    expect(await retryFollowUp(7)).toMatchObject({ id: 7, status: 'generating', failure: null })
    expect(world.requests[0]).toMatchObject({ method: 'POST', path: '/api/follow-ups/7/retry' })
    world.round.phase = 'processing'
    await act(5, { action: 'no_follow_up', from_phase: 'processing', attempt_id: 10 })
    expect(world.requests[1]).toMatchObject({
      path: '/api/rounds/5/actions',
      body: { action: 'no_follow_up', from_phase: 'processing', attempt_id: 10 },
    })
  })

  it('reads a result and retries', async () => {
    const world = setupRoundWorld()
    expect(await getResult(7)).toMatchObject({ attempt_id: 7, status: 'processing' })
    expect(await retryAttempt(7)).toMatchObject({ attempt_id: 7, status: 'processing' })
    expect(world.requests[0]).toMatchObject({ method: 'POST', path: '/api/attempts/7/retry' })
  })
})
