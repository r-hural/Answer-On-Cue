import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { startReading, type Playback } from './questionAudio'

const played = (): Playback => ({ ended: Promise.resolve(), stop() {} })

function captureSpeak(): { bodies: unknown[] } {
  const bodies: unknown[] = []
  server.use(
    http.post('/api/voice/speak', async ({ request }) => {
      bodies.push(await request.json())
      return HttpResponse.json({ audio_url: '/api/voice/cache/a.wav' })
    }),
  )
  return { bodies }
}

describe('startReading: the speak request', () => {
  it("sends the round's language", async () => {
    const { bodies } = captureSpeak()
    const playUrl = vi.fn(played)
    const reading = startReading(
      'Erklären Sie…',
      { mode: 'premium', provider: 'piper', voice: 'de_DE-thorsten-high', rate: 1, language: 'de' },
      { playUrl },
    )
    expect(await reading.done).toEqual({ playbackMs: expect.any(Number), failed: false })
    expect(bodies).toEqual([
      { text: 'Erklären Sie…', provider: 'piper', voice: 'de_DE-thorsten-high', rate: 1, language: 'de' },
    ])
    expect(playUrl).toHaveBeenCalledWith('/api/voice/cache/a.wav')
  })

  it('leaves the language out when the round has none', async () => {
    const { bodies } = captureSpeak()
    const reading = startReading(
      'Pytanie?',
      { mode: 'premium', provider: 'piper', voice: 'pl_PL-bass-high', rate: 1, language: null },
      { playUrl: played },
    )
    await reading.done
    expect(bodies).toEqual([{ text: 'Pytanie?', provider: 'piper', voice: 'pl_PL-bass-high', rate: 1 }])
    expect(bodies[0]).not.toHaveProperty('language')
  })

  it('browser and off make no request', async () => {
    const { bodies } = captureSpeak()
    const speakBrowser = vi.fn(played)
    await startReading('Frage?', { mode: 'browser', language: 'de' }, { speakBrowser }).done
    await startReading('Frage?', { mode: 'off', language: 'de' }, { speakBrowser }).done
    expect(speakBrowser).toHaveBeenCalledTimes(1)
    expect(bodies).toEqual([])
  })
})

describe('startReading: the browser voice', () => {
  afterEach(() => vi.unstubAllGlobals())

  function stubSpeech(voices: { lang: string }[]) {
    const speak = vi.fn((utterance: { onend: (() => void) | null }) => utterance.onend?.())
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        text: string
        lang = ''
        rate = 1
        voice: unknown = null
        onend: (() => void) | null = null
        onerror: ((e: { error: string }) => void) | null = null
        constructor(text: string) {
          this.text = text
        }
      },
    )
    vi.stubGlobal('speechSynthesis', { getVoices: () => voices, speak, cancel: vi.fn() })
    return speak
  }

  it("passes the language's BCP 47", async () => {
    const speakBrowser = vi.fn(played)
    await startReading('Frage?', { mode: 'browser', language: 'de' }, { speakBrowser }).done
    expect(speakBrowser).toHaveBeenLastCalledWith('Frage?', undefined, 'de-DE')
    await startReading('Q?', { mode: 'browser', language: null }, { speakBrowser }).done
    expect(speakBrowser).toHaveBeenLastCalledWith('Q?', undefined, 'pl-PL')
    await startReading('Q?', { mode: 'browser' }, { speakBrowser }).done
    expect(speakBrowser).toHaveBeenLastCalledWith('Q?', undefined, 'pl-PL')
  })

  it('uses a system voice of the language', async () => {
    const voice = { lang: 'de-DE' }
    const speak = stubSpeech([voice])
    const result = await startReading('Frage?', { mode: 'browser', language: 'de' }).done
    expect(result.failed).toBe(false)
    const utterance = speak.mock.calls[0][0] as unknown as { lang: string; voice: unknown }
    expect(utterance.lang).toBe('de-DE')
    expect(utterance.voice).toBe(voice)
  })

  it('speaks with the language tag alone when the system list is empty', async () => {
    const speak = stubSpeech([])
    await startReading('Frage?', { mode: 'browser', language: 'de' }).done
    const utterance = speak.mock.calls[0][0] as unknown as { lang: string; voice: unknown }
    expect(utterance.lang).toBe('de-DE')
    expect(utterance.voice).toBeNull()
  })

  it('fails when the system has voices but none for the language', async () => {
    const speak = stubSpeech([{ lang: 'pl-PL' }])
    const result = await startReading('Frage?', { mode: 'browser', language: 'de' }).done
    expect(result.failed).toBe(true)
    expect(speak).not.toHaveBeenCalled()
  })
})
