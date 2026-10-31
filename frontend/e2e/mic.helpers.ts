import type { Page } from '@playwright/test'

// e2e is type-checked without the DOM lib, so the in-page callbacks see the page through this.
interface PageGlobals {
  document: { querySelector(selector: string): { getAttribute(name: string): string | null } | null }
  performance: { now(): number }
  [key: string]: unknown
}

/**
 * Waits until the fake microphone has played something loud and then stays quiet for `quietMs`,
 * i.e. the looping fixture has just entered its silent part.
 */
export async function waitForLoudThenQuiet(page: Page, quietMs = 400, timeout = 20000): Promise<void> {
  const key = `__micWait${Math.random().toString(36).slice(2)}`
  await page.waitForFunction(
    ({ key, quietMs }) => {
      const g = globalThis as unknown as PageGlobals
      const state = (g[key] ??= { loud: false, quietSince: null }) as { loud: boolean; quietSince: number | null }
      const meter = g.document.querySelector('[role="meter"]')
      if (!meter) return false
      const db = Number(meter.getAttribute('aria-valuenow'))
      const now = g.performance.now()
      if (db > -60) {
        state.loud = true
        state.quietSince = null
        return false
      }
      if (!state.loud || db > -90) {
        state.quietSince = null
        return false
      }
      state.quietSince ??= now
      return now - state.quietSince >= quietMs
    },
    { key, quietMs },
    { polling: 50, timeout },
  )
}

/** Waits until the meter has shown a level above `db` at least once. */
export async function waitForLevelAbove(page: Page, db: number, timeout = 15000): Promise<void> {
  await page.waitForFunction(
    (db) => {
      const g = globalThis as unknown as PageGlobals
      return Number(g.document.querySelector('[role="meter"]')?.getAttribute('aria-valuenow') ?? -200) > db
    },
    db,
    { polling: 20, timeout },
  )
}
