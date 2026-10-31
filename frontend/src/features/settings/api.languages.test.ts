import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { ApiError } from '../../api/client'
import { fetchVoices, previewVoice, updateLanguage } from './api'

function record(method: 'get' | 'post' | 'put', path: string) {
  const seen: { url: string; body: unknown; guard: string | null }[] = []
  server.use(
    http[method](path, async ({ request }) => {
      const text = method === 'get' ? '' : await request.text()
      seen.push({
        url: request.url.replace(/^https?:\/\/[^/]+/, ''),
        body: text ? JSON.parse(text) : null,
        guard: request.headers.get('X-AnswerOnCue'),
      })
      return HttpResponse.json(method === 'get' ? [] : { audio_url: null })
    }),
  )
  return seen
}

describe('settings api: languages', () => {
  it('fetchVoices sends the language and refresh only when asked', async () => {
    const seen = record('get', '/api/voice/voices')
    await fetchVoices()
    await fetchVoices('en')
    await fetchVoices('en', { refresh: true })
    await fetchVoices(undefined, { refresh: true })
    expect(seen.map((r) => r.url)).toEqual([
      '/api/voice/voices',
      '/api/voice/voices?language=en',
      '/api/voice/voices?language=en&refresh=true',
      '/api/voice/voices',
    ])
  })

  it('previewVoice adds the language only when given', async () => {
    const seen = record('post', '/api/voice/preview')
    await previewVoice('piper', 'en_GB-cori-high', 1, 'en')
    await previewVoice('piper', 'pl_PL-gosia-medium', 1)
    expect(seen[0].body).toEqual({
      provider: 'piper',
      voice: 'en_GB-cori-high',
      rate: 1,
      language: 'en',
    })
    expect(seen[1].body).toEqual({ provider: 'piper', voice: 'pl_PL-gosia-medium', rate: 1 })
    expect('language' in (seen[1].body as object)).toBe(false)
  })

  it('updateLanguage puts the patch with the guard header', async () => {
    const seen = record('put', '/api/languages/en')
    await updateLanguage('en', { enabled: true })
    expect(seen[0].url).toBe('/api/languages/en')
    expect(seen[0].body).toEqual({ enabled: true })
    expect(seen[0].guard).toBe('1')
  })

  it('updateLanguage rejects with the server detail', async () => {
    server.use(
      http.put('/api/languages/pl', () =>
        HttpResponse.json({ detail: 'At least one language must stay enabled.' }, { status: 409 }),
      ),
    )
    const error = await updateLanguage('pl', { enabled: false }).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('At least one language must stay enabled.')
  })
})
