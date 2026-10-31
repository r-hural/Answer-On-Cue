import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { ContentLanguage } from '../library/api'
import { languagesWith } from '../library/languages.mocks'
import type { DownloadState, Preferences, VoiceInfo } from './api'
import { LanguageVoice } from './LanguageVoice'

const preferences: Preferences = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gpt-6.1-sol',
  assessment_provider: 'openai',
  assessment_model: 'gpt-6.1-sol',
  voice_provider: 'piper',
  voice_name: 'pl_PL-bass-high',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
  round_question_count: 10,
  round_prep_seconds: 30,
  round_answer_seconds: 120,
}

const v = (
  provider: VoiceInfo['provider'],
  id: string,
  label: string,
  installed: boolean,
  language: string | null = null,
): VoiceInfo => ({ provider, id, label, installed, gender: null, language })

const OPENAI = ['marin', 'cedar'].map((id) => v('openai', id, `${id} (OpenAI Realtime)`, false))
const BROWSER = v('browser', 'browser', 'Browser voice (built into the system)', true)

function voicesFor(code: string, cori = false): VoiceInfo[] {
  if (code === 'pl')
    return [
      v('piper', 'pl_PL-gosia-medium', 'Gosia (Piper, local)', false, 'pl'),
      v('piper', 'pl_PL-bass-high', 'Bass (Piper, local, high quality)', true, 'pl'),
      BROWSER,
    ]
  return [
    v('piper', 'en_GB-cori-high', 'Cori (Piper, local, high quality)', cori, 'en'),
    v('piper', 'en_GB-alba-medium', 'Alba (Piper, local)', true, 'en'),
    ...OPENAI,
    BROWSER,
  ]
}

const row = (code: string, patch: Partial<ContentLanguage> = {}): ContentLanguage => ({
  ...languagesWith('en').find((r) => r.code === code)!,
  ...patch,
})

interface Seen {
  voiceUrls: string[]
  puts: unknown[]
  previews: unknown[]
}

function backend(options: { cori?: boolean; putStatus?: number } = {}): Seen {
  const seen: Seen = { voiceUrls: [], puts: [], previews: [] }
  let cori = options.cori ?? false
  server.use(
    http.get('/api/voice/voices', ({ request }) => {
      const url = new URL(request.url)
      seen.voiceUrls.push(url.pathname + url.search)
      return HttpResponse.json(voicesFor(url.searchParams.get('language') ?? 'pl', cori))
    }),
    http.put('/api/languages/:code', async ({ request, params }) => {
      const body = (await request.json()) as object
      seen.puts.push(body)
      if (options.putStatus)
        return HttpResponse.json(
          { detail: 'This voice is for another language. Choose a voice for the content language.' },
          { status: options.putStatus },
        )
      return HttpResponse.json({ ...row(String(params.code)), ...body })
    }),
    http.post('/api/voice/preview', async ({ request }) => {
      seen.previews.push(await request.json())
      return HttpResponse.json({ audio_url: '/api/voice/cache/abc.wav' })
    }),
    http.post('/api/voice/downloads', () => {
      cori = true
      return HttpResponse.json(state({ bytes_done: 0 }), { status: 202 })
    }),
  )
  return seen
}

function state(patch: Partial<DownloadState>): DownloadState {
  return {
    voice: 'en_GB-cori-high',
    status: 'running',
    bytes_done: 0,
    bytes_total: 20 * 1024 * 1024,
    error: null,
    ...patch,
  }
}

function show(language: ContentLanguage, keysVersion = 0, onSaved = vi.fn()) {
  const ui = (version: number) => (
    <LanguageVoice
      language={language}
      preferences={preferences}
      keysVersion={version}
      onSaved={onSaved}
    />
  )
  const view = render(ui(keysVersion))
  return { onSaved, rerender: (version: number) => view.rerender(ui(version)) }
}

function stubAudio(played: string[]) {
  vi.stubGlobal(
    'Audio',
    class {
      constructor(public src: string) {}
      play() {
        played.push(this.src)
        return Promise.resolve()
      }
    },
  )
}

function stubSpeech(voices: { lang: string }[]) {
  const speak = vi.fn()
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

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('LanguageVoice', () => {
  it('lists only the language voices', async () => {
    const seen = backend()
    show(row('en', { voice_provider: 'piper', voice_name: 'en_GB-alba-medium' }))
    const select = await screen.findByLabelText('Voice')
    expect(select).toHaveAttribute('id', 'voice-en')
    await waitFor(() =>
      expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
        'Cori (Piper, local, high quality) (not downloaded)',
        'Alba (Piper, local)',
      ]),
    )
    expect(select).toHaveValue('en_GB-alba-medium')
    expect(screen.queryByRole('option', { name: /pl_PL-/ })).toBeNull()
    expect(seen.voiceUrls).toEqual(['/api/voice/voices?language=en'])
  })

  it('explains Default for a NULL row', async () => {
    backend()
    const { unmount } = render(
      <LanguageVoice language={row('pl')} preferences={preferences} keysVersion={0} onSaved={vi.fn()} />,
    )
    expect(screen.getByRole('radio', { name: 'Default' })).toBeChecked()
    expect(
      await screen.findByText(
        'Uses the voice from before languages existed: Piper (local) · Bass (Piper, local, high quality).',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Listen' })).toBeNull()
    unmount()
    show(row('en'))
    expect(
      screen.getByText('Uses the downloaded Piper voice of this language, else the browser voice.'),
    ).toBeInTheDocument()
  })

  it('saves both fields', async () => {
    const seen = backend()
    const { onSaved } = show(row('en'))
    await userEvent.click(screen.getByRole('radio', { name: 'Piper (local)' }))
    await userEvent.selectOptions(await screen.findByLabelText('Voice'), 'en_GB-alba-medium')
    await userEvent.click(screen.getByRole('button', { name: 'Save voice' }))
    expect(await screen.findByText('Voice saved.')).toBeInTheDocument()
    expect(seen.puts[0]).toEqual({ voice_provider: 'piper', voice_name: 'en_GB-alba-medium' })
    expect(onSaved).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'en', voice_name: 'en_GB-alba-medium' }),
    )
    await userEvent.click(screen.getByRole('radio', { name: 'Browser voice' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save voice' }))
    await waitFor(() => expect(seen.puts).toHaveLength(2))
    expect(seen.puts[1]).toEqual({ voice_provider: 'browser', voice_name: null })
    await userEvent.click(screen.getByRole('radio', { name: 'Default' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save voice' }))
    await waitFor(() => expect(seen.puts).toHaveLength(3))
    expect(seen.puts[2]).toEqual({ voice_provider: null })
  })

  it('shows the server refusal', async () => {
    backend({ putStatus: 422 })
    show(row('en'))
    await userEvent.click(screen.getByRole('radio', { name: 'Browser voice' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save voice' }))
    expect(
      await screen.findByText(
        'This voice is for another language. Choose a voice for the content language.',
      ),
    ).toBeInTheDocument()
  })

  it('downloads with progress', async () => {
    const seen = backend()
    let polls = 0
    server.use(
      http.get('/api/voice/downloads/:voice', () => {
        polls += 1
        if (polls === 1) return HttpResponse.json(state({ bytes_done: 5 * 1024 * 1024 }))
        return HttpResponse.json(state({ status: 'done', bytes_done: 20 * 1024 * 1024 }))
      }),
    )
    show(row('en', { voice_provider: 'piper', voice_name: 'en_GB-cori-high' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Download' }))
    expect(
      await screen.findByText('Downloaded 5.0 of 20.0 MB', {}, { timeout: 3000 }),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Download' })).toBeNull(), {
      timeout: 3000,
    })
    expect(seen.voiceUrls.filter((u) => u === '/api/voice/voices?language=en').length).toBe(2)
  })

  it('previews with the rate on the slider, saved or not', async () => {
    const seen = backend()
    stubAudio([])
    render(
      <LanguageVoice
        language={row('en', { voice_provider: 'piper', voice_name: 'en_GB-alba-medium' })}
        preferences={preferences}
        keysVersion={0}
        onSaved={vi.fn()}
        rate={1.4}
      />,
    )
    await screen.findByRole('option', { name: /Alba/ })
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    await waitFor(() => expect(seen.previews).toHaveLength(1))
    expect(seen.previews[0]).toMatchObject({ rate: 1.4 })
  })

  it('previews in the language', async () => {
    const seen = backend()
    const played: string[] = []
    stubAudio(played)
    show(row('en', { voice_provider: 'piper', voice_name: 'en_GB-alba-medium' }))
    await screen.findByRole('option', { name: /Alba/ })
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    await waitFor(() => expect(played).toEqual(['/api/voice/cache/abc.wav']))
    expect(seen.previews[0]).toEqual({
      provider: 'piper',
      voice: 'en_GB-alba-medium',
      rate: 1,
      language: 'en',
    })
    await userEvent.selectOptions(screen.getByLabelText('Voice'), 'en_GB-cori-high')
    expect(screen.getByRole('button', { name: 'Listen' })).toBeDisabled()
  })

  it('previews with the browser voice of the language', async () => {
    backend()
    const speak = stubSpeech([{ lang: 'en-GB' }])
    show(row('en', { voice_provider: 'browser' }))
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    const utterance = speak.mock.calls[0][0] as { lang: string; text: string }
    expect(utterance.lang).toBe('en-GB')
    expect(utterance.text).toMatch(/^Discuss the causes/)
  })

  it('says when the browser has no voice for the language', async () => {
    backend()
    const speak = stubSpeech([{ lang: 'pl-PL' }])
    show(row('en', { voice_provider: 'browser' }))
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    expect(await screen.findByText('This browser has no English voice.')).toBeInTheDocument()
    expect(speak).not.toHaveBeenCalled()
  })

  it('refreshes only on the button', async () => {
    const seen = backend()
    show(row('en'))
    await userEvent.click(screen.getByRole('radio', { name: 'Piper (local)' }))
    await screen.findByLabelText('Voice')
    expect(seen.voiceUrls.some((u) => u.includes('refresh=true'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Check for more voices' }))
    expect(await screen.findByText('Voice list updated.')).toBeInTheDocument()
    expect(seen.voiceUrls.filter((u) => u.includes('refresh=true'))).toEqual([
      '/api/voice/voices?language=en&refresh=true',
    ])
  })

  it('fetches the list again when the keys change', async () => {
    const seen = backend()
    const { rerender } = show(row('en'))
    await waitFor(() => expect(seen.voiceUrls).toHaveLength(1))
    rerender(1)
    await waitFor(() => expect(seen.voiceUrls).toHaveLength(2))
  })

  it('warns about Azure without voices and cannot save it', async () => {
    backend()
    show(row('en'))
    await userEvent.click(screen.getByRole('radio', { name: 'Azure Speech' }))
    expect(await screen.findByText('First add an Azure Speech key and region.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save voice' })).toBeDisabled()
  })
})
