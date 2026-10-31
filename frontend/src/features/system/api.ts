export interface UpdateStatus {
  ok: boolean
  version: string
  from_version: string | null
  message: string | null
  at: string
}

export interface SystemInfo {
  version: string
  installed: boolean
  latest: string | null
  update_available: boolean
  checked_at: string | null
  check_message: string | null
  running_jobs: number
  last_update: UpdateStatus | null
}

const GUARD_HEADERS = { 'X-AnswerOnCue': '1' }

export async function getInfo(refresh = false): Promise<SystemInfo> {
  const response = await fetch(`/api/system/info${refresh ? '?refresh=true' : ''}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return (await response.json()) as SystemInfo
}

export async function postShutdown(): Promise<void> {
  const response = await fetch('/api/system/shutdown', { method: 'POST', headers: GUARD_HEADERS })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}

/** Resolves to null when the update started, else to the server's message ('' when it has none). */
export async function postUpdate(): Promise<string | null> {
  const response = await fetch('/api/system/update', { method: 'POST', headers: GUARD_HEADERS })
  if (response.ok) return null
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // fall through to the generic message
  }
  return ''
}

export async function getHealthVersion(): Promise<string | null> {
  try {
    const response = await fetch('/api/health')
    if (!response.ok) return null
    const body = (await response.json()) as { version?: string }
    return body.version ?? null
  } catch {
    return null
  }
}
