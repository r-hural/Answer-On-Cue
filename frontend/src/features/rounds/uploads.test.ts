import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import type { AttemptEventsIn } from './api'
import { memoryOutbox, OUTBOX_TTL_MS } from './outbox'
import { createUploadQueue, guardPageLeave, OUTBOX_WAIT_MS } from './uploads'

const EVENTS: AttemptEventsIn = {
  question_shown_at: 1,
  question_ready_at: 1,
  playback_ms: 0,
  prep_end_at: 10,
  start_reason: 'auto',
  capture_start_at: 10,
  stopped_at: 20,
  pre_roll_ms: 800,
  interrupted: false,
  listening_available: true,
}

function kit(overrides: { events?: () => Promise<unknown>; audio?: () => Promise<unknown> } = {}) {
  const calls: string[] = []
  const sleeps: number[] = []
  const postEvents = vi.fn(async (attemptId: number) => {
    calls.push(`events ${attemptId}`)
    await overrides.events?.()
    return { attempt_id: attemptId, stored: true }
  })
  const uploadAnswer = vi.fn(async (attemptId: number) => {
    calls.push(`audio ${attemptId}`)
    await overrides.audio?.()
    return { attempt_id: attemptId, recording_id: 1, duration_ms: 1000 }
  })
  const queue = createUploadQueue({
    postEvents,
    uploadAnswer,
    sleep: async (ms) => {
      sleeps.push(ms)
    },
  })
  return { queue, calls, sleeps, postEvents, uploadAnswer }
}

const wav = () => new Blob([new Uint8Array(10)], { type: 'audio/wav' })

describe('upload queue', () => {
  it('sends the events before the audio, for each attempt', async () => {
    const { queue, calls } = kit()
    queue.enqueue(7, EVENTS, wav())
    queue.enqueue(8, EVENTS, wav())
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(calls.indexOf('events 7')).toBeLessThan(calls.indexOf('audio 7'))
    expect(calls.indexOf('events 8')).toBeLessThan(calls.indexOf('audio 8'))
    expect(calls).toHaveLength(4)
  })

  it('retries three times after 1, 3 and 9 seconds, then fails and keeps the audio', async () => {
    const down = () => Promise.reject(new ApiError(503, 'Server error (HTTP 503).'))
    const { queue, sleeps, postEvents, uploadAnswer } = kit({ events: down })
    const blob = wav()
    queue.enqueue(7, EVENTS, blob)
    await vi.waitFor(() => expect(queue.failed()).toHaveLength(1))
    expect(sleeps).toEqual([1000, 3000, 9000])
    expect(postEvents).toHaveBeenCalledTimes(4)
    expect(uploadAnswer).not.toHaveBeenCalled()
    expect(queue.failed()[0]).toMatchObject({ attemptId: 7, state: 'failed', error: 'Server error (HTTP 503).' })
    expect(queue.failed()[0].wav).toBe(blob)
    expect(queue.pending()).toBe(1)
  })

  it('"Try again" sends the failed upload again, without repeating stored events', async () => {
    let audioUp = false
    const { queue, postEvents, uploadAnswer } = kit({
      audio: () => (audioUp ? Promise.resolve() : Promise.reject(new ApiError(0, 'Cannot reach the server.'))),
    })
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.failed()).toHaveLength(1))
    expect(postEvents).toHaveBeenCalledTimes(1)
    expect(uploadAnswer).toHaveBeenCalledTimes(4)
    audioUp = true
    queue.retry(7)
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(postEvents).toHaveBeenCalledTimes(1)
    expect(uploadAnswer).toHaveBeenCalledTimes(5)
    expect(queue.failed()).toEqual([])
  })

  it('does not wait on an answer the server refused', async () => {
    const refused = () => Promise.reject(new ApiError(409, 'Different events were already saved for this attempt.'))
    const { queue, sleeps, postEvents } = kit({ events: refused })
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.failed()).toHaveLength(1))
    expect(sleeps).toEqual([])
    expect(postEvents).toHaveBeenCalledTimes(1)
  })

  it('notifies subscribers', async () => {
    const { queue } = kit()
    const listener = vi.fn()
    const off = queue.subscribe(listener)
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(listener).toHaveBeenCalled()
    off()
  })
})

describe('guardPageLeave', () => {
  function leave(target: Window): boolean {
    const event = new Event('beforeunload', { cancelable: true })
    target.dispatchEvent(event)
    return event.defaultPrevented
  }

  it('warns while an upload is pending and stays quiet otherwise', async () => {
    let finish: () => void = () => {}
    let started = false
    const slow = () =>
      new Promise<void>((resolve) => {
        started = true
        finish = resolve
      })
    const { queue } = kit({ audio: slow })
    const target = new EventTarget() as unknown as Window
    const remove = guardPageLeave(queue, target)
    expect(leave(target)).toBe(false)
    queue.enqueue(7, EVENTS, wav())
    expect(leave(target)).toBe(true)
    await vi.waitFor(() => expect(started).toBe(true))
    expect(queue.pending()).toBe(1)
    finish()
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(leave(target)).toBe(false)
    queue.enqueue(8, EVENTS, wav())
    remove()
    expect(leave(target)).toBe(false)
  })
})

describe('upload queue with a durable outbox', () => {
  function stored(overrides: Parameters<typeof kit>[0] = {}, now = () => 1_000, attemptRound: (attemptId: number) => Promise<number> = async () => 0) {
    const store = memoryOutbox()
    const calls: string[] = []
    const postEvents = vi.fn(async (attemptId: number) => {
      calls.push(`events ${attemptId}`)
      await overrides.events?.()
      return { attempt_id: attemptId, stored: true }
    })
    const uploadAnswer = vi.fn(async (attemptId: number) => {
      calls.push(`audio ${attemptId}`)
      await overrides.audio?.()
      return { attempt_id: attemptId, recording_id: 1, duration_ms: 1000 }
    })
    const queue = createUploadQueue({ postEvents, uploadAnswer, sleep: async () => {}, store, now, attemptRound })
    return { queue, store, calls, postEvents, uploadAnswer }
  }

  it('stores the answer before the first request', async () => {
    let seen: number[] | null = null
    const { queue, store } = stored({
      events: async () => {
        seen = (await store.all()).map((e) => e.attemptId)
      },
    })
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(seen).toEqual([7])
  })

  it('removes the stored answer once the server has it', async () => {
    const { queue, store } = stored()
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    await vi.waitFor(async () => expect(await store.all()).toEqual([]))
  })

  it('keeps it, failed, when the network stays down after the retries', async () => {
    const { queue, store } = stored({ audio: () => Promise.reject(new ApiError(0, 'Cannot reach the server.')) })
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.failed()).toHaveLength(1))
    const [entry] = await store.all()
    expect(entry).toMatchObject({ attemptId: 7, eventsStored: true, savedAt: 1_000 })
  })

  it('restore() replays stored answers once and skips events already stored', async () => {
    const { queue, store, calls } = stored()
    await store.put({ attemptId: 7, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: false })
    await store.put({ attemptId: 8, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: true })
    expect(await queue.restore()).toBe(2)
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(await queue.restore()).toBe(0)
    expect(calls.sort()).toEqual(['audio 7', 'audio 8', 'events 7'])
    expect(await store.all()).toEqual([])
  })

  it('drops stored answers older than the outbox lifetime, unsent', async () => {
    const { queue, store, calls } = stored({}, () => OUTBOX_TTL_MS + 2_000)
    await store.put({ attemptId: 7, events: EVENTS, wav: wav(), savedAt: 1_000, eventsStored: false })
    expect(await queue.restore()).toBe(0)
    expect(calls).toEqual([])
    expect(await store.all()).toEqual([])
  })

  it('stores the round with the answer', async () => {
    let seen: unknown[] = []
    const { queue, store } = stored({
      events: async () => {
        seen = await store.all()
      },
    })
    queue.enqueue(7, EVENTS, wav(), 3)
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(seen).toMatchObject([{ attemptId: 7, roundId: 3 }])
  })

  it('replays a stored answer only into its round: another round or a 404 drops it unsent', async () => {
    const rounds: Record<number, number> = { 7: 3, 8: 99 }
    const attemptRound = vi.fn(async (attemptId: number) => {
      if (!(attemptId in rounds)) throw new ApiError(404, 'Attempt not found.')
      return rounds[attemptId]
    })
    const { queue, store, calls } = stored({}, () => 1_000, attemptRound)
    await store.put({ attemptId: 7, roundId: 3, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: false })
    await store.put({ attemptId: 8, roundId: 4, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: false })
    await store.put({ attemptId: 9, roundId: 5, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: false })
    expect(await queue.restore()).toBe(1)
    await vi.waitFor(() => expect(queue.pending()).toBe(0))
    expect(calls.sort()).toEqual(['audio 7', 'events 7'])
    expect(queue.failed()).toEqual([])
    expect(await store.all()).toEqual([])
  })

  it('keeps a stored answer for the next page when its round cannot be checked', async () => {
    const attemptRound = vi.fn(async () => {
      throw new ApiError(0, 'Network down.')
    })
    const { queue, store, calls } = stored({}, () => 1_000, attemptRound)
    await store.put({ attemptId: 7, roundId: 3, events: EVENTS, wav: wav(), savedAt: 900, eventsStored: false })
    expect(await queue.restore()).toBe(0)
    expect(calls).toEqual([])
    expect(await store.all()).toHaveLength(1)
  })

  it.each([404, 409, 413, 415, 422])('drops the stored answer on HTTP %i', async (status) => {
    const { queue, store } = stored({ audio: () => Promise.reject(new ApiError(status, 'Refused.')) })
    queue.enqueue(7, EVENTS, wav())
    await vi.waitFor(() => expect(queue.failed()).toHaveLength(1))
    await vi.waitFor(async () => expect(await store.all()).toEqual([]))
  })
})

describe('upload queue with a hung outbox', () => {
  it('still sends the answer when the outbox never answers', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const never = new Promise<never>(() => {})
      const store = { put: () => never, remove: () => never, all: () => never }
      const { queue, calls } = kit()
      const hung = createUploadQueue({
        postEvents: async (attemptId: number) => {
          calls.push(`events ${attemptId}`)
          return { attempt_id: attemptId, stored: true }
        },
        uploadAnswer: async (attemptId: number) => {
          calls.push(`audio ${attemptId}`)
          return { attempt_id: attemptId, recording_id: 1, duration_ms: 1000 }
        },
        sleep: async () => {},
        store,
      })
      expect(queue.pending()).toBe(0)
      hung.enqueue(7, EVENTS, wav())
      await vi.advanceTimersByTimeAsync(OUTBOX_WAIT_MS)
      await vi.waitFor(() => expect(hung.pending()).toBe(0))
      expect(calls).toEqual(['events 7', 'audio 7'])
    } finally {
      vi.useRealTimers()
    }
  })
})
