import { ApiError } from '../../api/client'
import { getAttemptRound, postEvents, uploadAnswer, type AttemptEventsIn } from './api'
import { defaultOutbox, OUTBOX_TTL_MS, type OutboxEntry, type OutboxStore } from './outbox'

export type UploadState = 'sending' | 'failed' | 'done'

export interface UploadItem {
  attemptId: number
  /** The attempt's round, stored with the answer and checked before a replay. */
  roundId?: number
  events: AttemptEventsIn
  /** Kept in memory until the server answered 2xx. */
  wav: Blob
  state: UploadState
  /** English reason of the last failure. */
  error: string | null
}

export interface UploadDeps {
  postEvents: typeof postEvents
  uploadAnswer: typeof uploadAnswer
  sleep(ms: number): Promise<void>
  /** Waits before retry 1, 2, 3. */
  backoffMs: number[]
  /** Keeps each answer until the server has it, so a reload loses nothing. */
  store?: OutboxStore
  /** Date.now, for the outbox's lifetime. */
  now?: () => number
  /** The round the server has the attempt in (404 when it has no such attempt). */
  attemptRound: (attemptId: number) => Promise<number>
}

export interface UploadQueue {
  /** Sends the events, then the audio, of one answer in the background. */
  enqueue(attemptId: number, events: AttemptEventsIn, wav: Blob, roundId?: number): void
  /** Starts a failed upload again (events already stored are not sent twice); true once it went through. */
  retry(attemptId: number): Promise<boolean>
  /** Uploads that are not finished: sending or failed. */
  pending(): number
  failed(): UploadItem[]
  subscribe(listener: () => void): () => void
  /** Sends again the answers a previous page left in the outbox (once per queue); how many. */
  restore(): Promise<number>
}

const defaultDeps: UploadDeps = {
  postEvents,
  uploadAnswer,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  backoffMs: [1000, 3000, 9000],
  store: defaultOutbox(),
  now: Date.now,
  attemptRound: async (attemptId) => (await getAttemptRound(attemptId)).round_id,
}

/** A client error other than "too early" or "too many requests" will not change by waiting. */
function worthRetrying(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true
  return error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500
}

/** The server will never take this answer: the attempt is gone, has no answer, or the audio is unusable. */
const UNUSABLE = [404, 409, 413, 415, 422]

function unusable(error: unknown): boolean {
  return error instanceof ApiError && UNUSABLE.includes(error.status)
}

/** The outbox is never waited on for longer than this: a stuck IndexedDB must not hold an answer back. */
export const OUTBOX_WAIT_MS = 2000

function bounded<T>(work: Promise<T>): Promise<T | undefined> {
  return Promise.race([work, new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), OUTBOX_WAIT_MS))])
}

type Item = UploadItem & { eventsStored: boolean; savedAt: number; saved: Promise<void> }

export function createUploadQueue(overrides: Partial<UploadDeps> = {}): UploadQueue {
  const deps: UploadDeps = { ...defaultDeps, ...overrides }
  const store = deps.store ?? defaultOutbox()
  const now = deps.now ?? Date.now
  const items = new Map<number, Item>()
  let restored: Promise<number> | null = null
  const listeners = new Set<() => void>()
  let failedSnapshot: UploadItem[] = []

  const notify = () => {
    failedSnapshot = [...items.values()].filter((item) => item.state === 'failed')
    for (const listener of listeners) listener()
  }

  /** One request with up to three retries after 1, 3 and 9 seconds. */
  async function withRetries(send: () => Promise<unknown>): Promise<void> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        await send()
        return
      } catch (error) {
        if (attempt >= deps.backoffMs.length || !worthRetrying(error)) throw error
        await deps.sleep(deps.backoffMs[attempt])
      }
    }
  }

  const save = (item: Item): Promise<void> =>
    bounded(
      store.put({
      attemptId: item.attemptId,
      roundId: item.roundId,
      events: item.events,
      wav: item.wav,
      savedAt: item.savedAt,
        eventsStored: item.eventsStored,
      }),
    ).then(() => undefined)
  const forget = (attemptId: number): Promise<void> => bounded(store.remove(attemptId)).then(() => undefined)

  /**
   * A stored answer is replayed only into the round it was recorded in: after a database restore
   * or reset the same attempt id can name another attempt. A mismatch or 404 drops it unsent; a
   * failed check keeps it for the next page. Entries stored without a round id are replayed.
   */
  async function belongs(entry: OutboxEntry): Promise<boolean> {
    if (entry.roundId === undefined) return true
    try {
      if ((await deps.attemptRound(entry.attemptId)) === entry.roundId) return true
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) return false
    }
    await forget(entry.attemptId)
    return false
  }

  async function run(item: Item): Promise<void> {
    item.state = 'sending'
    item.error = null
    notify()
    await item.saved // stored before the first request
    try {
      if (!item.eventsStored) {
        await withRetries(() => deps.postEvents(item.attemptId, item.events))
        item.eventsStored = true
        item.saved = save(item)
      }
      await withRetries(() => deps.uploadAnswer(item.attemptId, item.wav))
      item.state = 'done'
      items.delete(item.attemptId)
      await item.saved
      await forget(item.attemptId)
    } catch (error) {
      item.state = 'failed'
      item.error = error instanceof Error ? error.message : 'The upload failed.'
      if (unusable(error)) {
        await item.saved
        await forget(item.attemptId)
      }
    }
    notify()
  }

  function track(
    attemptId: number,
    roundId: number | undefined,
    events: AttemptEventsIn,
    wav: Blob,
    savedAt: number,
    eventsStored: boolean,
  ): Item {
    const item: Item = { attemptId, roundId, events, wav, state: 'sending', error: null, eventsStored, savedAt, saved: Promise.resolve() }
    items.set(attemptId, item)
    return item
  }

  return {
    enqueue(attemptId, events, wav, roundId) {
      const item = track(attemptId, roundId, events, wav, now(), false)
      item.saved = save(item)
      void run(item)
    },
    async retry(attemptId) {
      const item = items.get(attemptId)
      if (!item || item.state !== 'failed') return false
      await run(item)
      return !items.has(attemptId)
    },
    pending: () => items.size,
    failed: () => failedSnapshot,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    restore() {
      restored ??= (async () => {
        let replayed = 0
        for (const entry of (await bounded(store.all())) ?? []) {
          if (now() - entry.savedAt > OUTBOX_TTL_MS) {
            await forget(entry.attemptId)
            continue
          }
          if (items.has(entry.attemptId)) continue
          if (!(await belongs(entry)) || items.has(entry.attemptId)) continue
          void run(track(entry.attemptId, entry.roundId, entry.events, entry.wav, entry.savedAt, entry.eventsStored))
          replayed += 1
        }
        return replayed
      })()
      const first = restored
      restored = first.then(() => 0) // a second call replays nothing
      return first
    },
  }
}

/** The queue of the running app: it outlives the exam page, so a finished round keeps uploading. */
export const uploadQueue = createUploadQueue()

/** Warns before the page is left while an answer is still on its way. Returns the remover. */
export function guardPageLeave(queue: UploadQueue, target: Window = window): () => void {
  const handler = (event: BeforeUnloadEvent) => {
    if (queue.pending() === 0) return
    event.preventDefault()
    event.returnValue = ''
  }
  target.addEventListener('beforeunload', handler)
  return () => target.removeEventListener('beforeunload', handler)
}
