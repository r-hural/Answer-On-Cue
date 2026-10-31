export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** The server accepts guarded changes only with this header (CSRF, DNS rebinding). */
const GUARD_HEADERS = { 'X-AnswerOnCue': '1' }

function messageOf(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return `Server error (HTTP ${status}).`
}

async function request<T>(method: Method, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    const headers = method === 'GET' ? init.headers : { ...GUARD_HEADERS, ...init.headers }
    response = await fetch(`/api${path}`, { method, ...init, headers })
  } catch {
    throw new ApiError(0, 'Cannot reach the server.')
  }
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      // not JSON: the generic message below
    }
    throw new ApiError(response.status, messageOf(response.status, detail), detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>('GET', path)
}

export function apiSend<T>(
  method: Exclude<Method, 'GET'>,
  path: string,
  body?: unknown,
): Promise<T> {
  if (body === undefined) return request<T>(method, path)
  return request<T>(method, path, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** The browser sets the multipart content type (with its boundary) itself. */
export function apiUpload<T>(path: string, form: FormData): Promise<T> {
  return request<T>('POST', path, { body: form })
}
