import { describe, expect, it } from 'vitest'
import type { AttemptEventsIn } from './api'
import { defaultOutbox, memoryOutbox, OUTBOX_TTL_MS } from './outbox'

const EVENTS = { stopped_at: 20 } as AttemptEventsIn
const wav = () => new Blob([new Uint8Array(4)], { type: 'audio/wav' })

describe('answer outbox', () => {
  it('memoryOutbox keeps entries until they are removed', async () => {
    const store = memoryOutbox()
    const blob = wav()
    await store.put({ attemptId: 7, events: EVENTS, wav: blob, savedAt: 1, eventsStored: false })
    await store.put({ attemptId: 8, events: EVENTS, wav: wav(), savedAt: 2, eventsStored: false })
    await store.put({ attemptId: 7, events: EVENTS, wav: blob, savedAt: 1, eventsStored: true })
    const all = await store.all()
    expect(all.map((e) => e.attemptId).sort()).toEqual([7, 8])
    expect(all.find((e) => e.attemptId === 7)).toMatchObject({ eventsStored: true, wav: blob })
    await store.remove(7)
    expect((await store.all()).map((e) => e.attemptId)).toEqual([8])
  })

  it('is a no-op store where the browser has no IndexedDB (jsdom)', async () => {
    expect(typeof indexedDB).toBe('undefined')
    const store = defaultOutbox()
    await store.put({ attemptId: 7, events: EVENTS, wav: wav(), savedAt: 1, eventsStored: false })
    expect(await store.all()).toEqual([])
  })

  it('keeps an answer for seven days', () => {
    expect(OUTBOX_TTL_MS).toBe(7 * 24 * 60 * 60 * 1000)
  })
})
