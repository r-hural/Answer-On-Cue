import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { ContentLanguage } from '../library/api'
import { LANGUAGES } from '../library/languages.mocks'
import type { Preferences, VoiceInfo } from './api'
import { LanguageVoice } from './LanguageVoice'

const NOT_VERBATIM = 'The OpenAI voice did not read this question exactly, so it is shown as text.'

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

function voices(installed: boolean): VoiceInfo[] {
  return [
    { provider: 'piper', id: 'pl_PL-bass-high', label: 'Bass (Piper, local, high quality)', installed: true, gender: 'male' },
    { provider: 'azure_speech', id: 'pl-PL-ZofiaNeural', label: 'Zofia (Azure, neural)', installed: false, gender: 'female' },
    { provider: 'openai', id: 'marin', label: 'Marin (OpenAI Realtime, recommended)', installed, gender: 'female' },
    { provider: 'openai', id: 'cedar', label: 'Cedar (OpenAI Realtime, recommended)', installed, gender: 'male' },
    { provider: 'openai', id: 'alloy', label: 'Alloy (OpenAI Realtime)', installed, gender: 'female' },
    { provider: 'browser', id: 'browser', label: 'Browser voice (built into the system)', installed: true, gender: null },
  ]
}

interface Seen {
  method: string
  path: string
  body: unknown
}

function backend(options: { installed?: boolean; previewStatus?: number } = {}) {
  const seen: Seen[] = []
  server.use(
    http.get('/api/voice/voices', () => HttpResponse.json(voices(options.installed ?? false))),
    http.put('/api/languages/pl', async ({ request }) => {
      const body = (await request.json()) as object
      seen.push({ method: 'PUT', path: '/api/languages/pl', body })
      return HttpResponse.json({ ...LANGUAGES[0], ...body })
    }),
    http.post('/api/voice/preview', async ({ request }) => {
      seen.push({ method: 'POST', path: '/api/voice/preview', body: await request.json() })
      if (options.previewStatus) {
        return HttpResponse.json({ detail: NOT_VERBATIM }, { status: options.previewStatus })
      }
      return HttpResponse.json({ audio_url: '/api/voice/cache/abc.wav' })
    }),
  )
  return seen
}

function show(patch: Partial<ContentLanguage> = {}) {
  return render(
    <LanguageVoice
      language={{ ...LANGUAGES[0], ...patch }}
      preferences={preferences}
      keysVersion={0}
      onSaved={vi.fn()}
    />,
  )
}

async function chooseOpenAI() {
  await userEvent.click(screen.getByRole('radio', { name: 'OpenAI Realtime' }))
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the OpenAI Realtime voice in a language row', () => {
  it('lists OpenAI Realtime between Azure Speech and Browser voice', () => {
    backend()
    show()
    const radios = screen.getAllByRole('radio')
    expect(radios.map((radio) => radio.parentElement?.textContent)).toEqual([
      'Default',
      'Piper (local)',
      'Azure Speech',
      'OpenAI Realtime',
      'Browser voice',
    ])
  })

  it('OpenAI voices without a key', async () => {
    backend({ installed: false })
    const { container } = show()
    await chooseOpenAI()
    await waitFor(() =>
      expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
        'Marin (OpenAI Realtime, recommended) (no OpenAI key)',
        'Cedar (OpenAI Realtime, recommended) (no OpenAI key)',
        'Alloy (OpenAI Realtime) (no OpenAI key)',
      ]),
    )
    expect(container.querySelector('.notice.warn')).toHaveTextContent(
      'First add an OpenAI API key under API keys.',
    )
    expect(screen.getByRole('button', { name: 'Listen' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull()
  })

  it('OpenAI hint', async () => {
    backend({ installed: true })
    show()
    const hint = /Reads the exact question text through OpenAI Realtime/
    expect(screen.queryByText(hint)).toBeNull()
    await chooseOpenAI()
    expect(screen.getByText(hint)).toHaveClass('hint')
    await userEvent.click(screen.getByRole('radio', { name: 'Azure Speech' }))
    expect(screen.queryByText(hint)).toBeNull()
  })

  it('previews and saves an OpenAI voice', async () => {
    const seen = backend({ installed: true })
    const played: string[] = []
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
    const { container } = show()
    await chooseOpenAI()
    await screen.findByRole('option', { name: /Cedar/ })
    await userEvent.selectOptions(screen.getByLabelText('Voice'), 'cedar')
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    await waitFor(() => expect(played).toEqual(['/api/voice/cache/abc.wav']))
    expect(seen[0]).toEqual({
      method: 'POST',
      path: '/api/voice/preview',
      body: { provider: 'openai', voice: 'cedar', rate: 1, language: 'pl' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Save voice' }))
    expect(await screen.findByText('Voice saved.')).toBeInTheDocument()
    expect(seen[1].body).toEqual({ voice_provider: 'openai', voice_name: 'cedar' })
    expect(container.querySelector('.btn.primary')).toBeNull()
  })

  it('shows a reading that failed the check', async () => {
    backend({ installed: true, previewStatus: 409 })
    const { container } = show()
    await chooseOpenAI()
    await screen.findByRole('option', { name: /Marin/ })
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    expect(await within(container).findByText(NOT_VERBATIM)).toBeInTheDocument()
  })

  it('keeps the saved OpenAI voice', async () => {
    backend({ installed: true })
    show({ voice_provider: 'openai', voice_name: 'cedar' })
    expect(screen.getByRole('radio', { name: 'OpenAI Realtime' })).toBeChecked()
    await waitFor(() => expect(screen.getByLabelText('Voice')).toHaveValue('cedar'))
  })
})
