import type { AttemptEventsIn } from './api'

/** An answer is kept in this browser for at most seven days while it waits to be uploaded. */
export const OUTBOX_TTL_MS = 7 * 24 * 60 * 60 * 1000

export interface OutboxEntry {
  attemptId: number
  /** The attempt's round, checked before a replay (a database restore or reset can reuse the attempt id). Absent in older entries. */
  roundId?: number
  events: AttemptEventsIn
  wav: Blob
  /** Date.now() when the answer was stored. */
  savedAt: number
  /** The server already has the events: a replay sends only the audio. */
  eventsStored: boolean
}

/** Where answers wait until the server has accepted them; never throws. */
export interface OutboxStore {
  put(entry: OutboxEntry): Promise<void>
  remove(attemptId: number): Promise<void>
  all(): Promise<OutboxEntry[]>
}

export function memoryOutbox(): OutboxStore {
  const entries = new Map<number, OutboxEntry>()
  return {
    async put(entry) {
      entries.set(entry.attemptId, entry)
    },
    async remove(attemptId) {
      entries.delete(attemptId)
    },
    async all() {
      return [...entries.values()]
    },
  }
}

const noopOutbox: OutboxStore = {
  put: async () => {},
  remove: async () => {},
  all: async () => [],
}

const DB_NAME = 'aoc-outbox'
const STORE = 'answers'

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/** IndexedDB (database aoc-outbox, store answers): survives a reload or a closed tab. */
export function idbOutbox(): OutboxStore {
  let opened: Promise<IDBDatabase> | null = null
  const open = () => {
    opened ??= new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1)
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: 'attemptId' })
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => {
        opened = null
        reject(req.error)
      }
    })
    return opened
  }
  async function withStore<T>(mode: IDBTransactionMode, pick: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await open()
    const tx = db.transaction(STORE, mode)
    const done = new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
    const [result] = await Promise.all([request(pick(tx.objectStore(STORE))), done])
    return result
  }
  return {
    async put(entry) {
      try {
        await withStore('readwrite', (store) => store.put(entry))
      } catch {
        // Storage full or blocked (private window): the answer still uploads from memory.
      }
    },
    async remove(attemptId) {
      try {
        await withStore('readwrite', (store) => store.delete(attemptId))
      } catch {
        // Nothing to clean up that a later replay would not drop.
      }
    },
    async all() {
      try {
        return (await withStore('readonly', (store) => store.getAll())) as OutboxEntry[]
      } catch {
        return []
      }
    },
  }
}

/** IndexedDB in the browser; the no-op store where there is none (jsdom in tests). */
export function defaultOutbox(): OutboxStore {
  return typeof indexedDB === 'undefined' ? noopOutbox : idbOutbox()
}
