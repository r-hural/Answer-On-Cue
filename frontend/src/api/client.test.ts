import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/msw'
import { ApiError, apiGet, apiSend, apiUpload } from './client'

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    return error as ApiError
  }
  throw new Error('expected a failure')
}

describe('api client', () => {
  it('marks changes as coming from the app page, but not reads', async () => {
    const seen: Record<string, string | null> = {}
    server.use(
      http.get('/api/x', ({ request }) => {
        seen.get = request.headers.get('X-AnswerOnCue')
        return HttpResponse.json({})
      }),
      http.put('/api/x', ({ request }) => {
        seen.put = request.headers.get('X-AnswerOnCue')
        return HttpResponse.json({})
      }),
      http.delete('/api/x', ({ request }) => {
        seen.delete = request.headers.get('X-AnswerOnCue')
        return HttpResponse.json({})
      }),
    )
    await apiGet('/x')
    await apiSend('PUT', '/x', { a: 1 })
    await apiSend('DELETE', '/x')
    expect(seen).toEqual({ get: null, put: '1', delete: '1' })
  })

  it('uses a string detail as the message', async () => {
    server.use(http.get('/api/x', () => HttpResponse.json({ detail: 'Too short.' }, { status: 422 })))
    const error = await failure(apiGet('/x'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('Too short.')
    expect(error.status).toBe(422)
    expect(error.detail).toBe('Too short.')
  })

  it('uses detail.message of an object detail', async () => {
    server.use(
      http.get('/api/x', () =>
        HttpResponse.json({ detail: { message: 'The file is empty.', code: 1 } }, { status: 400 }),
      ),
    )
    expect((await failure(apiGet('/x'))).message).toBe('The file is empty.')
  })

  it('falls back to a generic message for a non-JSON error', async () => {
    server.use(http.get('/api/x', () => new HttpResponse('<html>oops</html>', { status: 500 })))
    expect((await failure(apiGet('/x'))).message).toBe('Server error (HTTP 500).')
  })

  it('does not show a validation list as text', async () => {
    server.use(http.get('/api/x', () => HttpResponse.json({ detail: [{ msg: 'x' }] }, { status: 422 })))
    expect((await failure(apiGet('/x'))).message).toBe('Server error (HTTP 422).')
  })

  it('returns undefined for 204', async () => {
    server.use(http.delete('/api/x', () => new HttpResponse(null, { status: 204 })))
    expect(await apiSend('DELETE', '/x')).toBeUndefined()
  })

  it('sends JSON bodies', async () => {
    let seen: unknown
    let type: string | null = null
    server.use(
      http.put('/api/x', async ({ request }) => {
        seen = await request.json()
        type = request.headers.get('content-type')
        return HttpResponse.json({ ok: true })
      }),
    )
    expect(await apiSend('PUT', '/x', { a: 1 })).toEqual({ ok: true })
    expect(seen).toEqual({ a: 1 })
    expect(type).toContain('application/json')
  })

  it('uploads FormData without a JSON content type', async () => {
    let type: string | null = null
    let body = ''
    server.use(
      http.post('/api/up', async ({ request }) => {
        type = request.headers.get('content-type')
        body = await request.text()
        return HttpResponse.json({ id: 1 }, { status: 201 })
      }),
    )
    const form = new FormData()
    form.append('file', new File(['abc'], 'a.txt', { type: 'text/plain' }))
    form.append('title', 'Document')
    expect(await apiUpload('/up', form)).toEqual({ id: 1 })
    expect(type).toContain('multipart/form-data')
    expect(type).not.toContain('json')
    expect(body).toContain('name="title"')
    expect(body).toContain('Document')
  })

  it('reports a network failure in English', async () => {
    server.use(http.get('/api/x', () => HttpResponse.error()))
    const error = await failure(apiGet('/x'))
    expect(error.status).toBe(0)
    expect(error.message).toBe('Cannot reach the server.')
  })
})
