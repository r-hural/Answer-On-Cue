import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { languagesWith } from '../library/languages.mocks'
import { SetupPage } from './SetupPage'
import { setupRoundWorld, type RoundWorld } from './rounds.mocks'

const PREFS = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gen-1',
  assessment_provider: 'anthropic',
  assessment_model: 'asm-1',
  voice_provider: 'piper',
  voice_name: 'pl_PL-bass-high',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
}

const voice = (provider: string, id: string, label: string, installed: boolean) => ({
  provider,
  id,
  label,
  installed,
  gender: null,
})
const BROWSER = voice('browser', 'browser', 'Browser voice (built into the system)', true)
const POLISH = [
  voice('piper', 'pl_PL-bass-high', 'Bass (Piper, local, high quality)', true),
  voice('openai', 'marin', 'Marin (OpenAI Realtime)', true),
  voice('openai', 'cedar', 'Cedar (OpenAI Realtime)', true),
  BROWSER,
]
const ENGLISH = [
  voice('piper', 'en_GB-cori-high', 'Cori (Piper, local, high quality)', true),
  voice('piper', 'en_GB-alba-medium', 'Alba (Piper, local)', false),
  voice('openai', 'cedar', 'Cedar (OpenAI Realtime)', true),
  BROWSER,
]

const SUBJECTS = [
  { id: 1, name: 'Historia', description: null, document_count: 1, language: 'pl' },
  { id: 2, name: 'History', description: null, document_count: 1, language: 'en' },
]

const topic = (id: number, title: string) => ({
  id,
  title,
  description: null,
  origin: 'extracted',
  status: 'active',
  objectives: [],
  passage_count: 3,
  question_count: 6,
  children: [],
})

const question = (id: number) => ({
  version_id: id,
  family_id: id,
  version: 1,
  text: `Question ${id}`,
  style: 'short',
  difficulty: 'recall',
  primary_topic_id: 11,
  status: 'active',
})

interface Options {
  prefs?: Record<string, unknown>
  english?: unknown[]
  polish?: unknown[]
  languages?: ReturnType<typeof languagesWith>
  /** Holds the English voice answer until the test resolves it. */
  hold?: { promise: Promise<void> }
}

function mockApi(options: Options = {}) {
  const world: RoundWorld = setupRoundWorld()
  const urls: string[] = []
  const previews: unknown[] = []
  server.use(
    http.get('/api/subjects', () => HttpResponse.json(SUBJECTS)),
    http.get('/api/settings', () => HttpResponse.json(options.prefs ?? PREFS)),
    http.get('/api/languages', () => HttpResponse.json(options.languages ?? languagesWith('en'))),
    http.get('/api/voice/voices', async ({ request }) => {
      const url = new URL(request.url)
      urls.push(url.pathname + url.search)
      const code = url.searchParams.get('language')
      if (code === 'en') {
        await options.hold?.promise
        return HttpResponse.json(options.english ?? ENGLISH)
      }
      return HttpResponse.json(options.polish ?? POLISH)
    }),
    http.get('/api/topics', ({ request }) => {
      const id = new URL(request.url).searchParams.get('subject_id')
      return HttpResponse.json([topic(11, id === '2' ? 'Athenian democracy' : 'Reformy a w Atenach')])
    }),
    http.get('/api/subjects/:id/documents', () =>
      HttpResponse.json([{ id: 21, title: 'Ateny a Rzym', latest_version: { passage_count: 5 }, version_count: 1 }]),
    ),
    http.get('/api/questions', () => HttpResponse.json(Array.from({ length: 12 }, (_, i) => question(i + 1)))),
    http.post('/api/voice/preview', async ({ request }) => {
      previews.push(await request.json())
      return HttpResponse.json({ audio_url: '/api/voice/cache/abc.wav' })
    }),
  )
  return { world, urls, previews }
}

function renderPage(path = '/practice') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/practice" element={<SetupPage />} />
        <Route path="/practice/:roundId/microphone" element={<p>Microphone page</p>} />
        <Route path="/settings" element={<p>Settings page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const audio = () => screen.getByLabelText('Read questions aloud') as HTMLSelectElement
const voiceSelect = () => screen.getByLabelText('Voice') as HTMLSelectElement
const optionIds = (select: HTMLElement) =>
  within(select).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)

async function chooseSubject(name: string) {
  await userEvent.selectOptions(await screen.findByLabelText('Subject'), name)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('SetupPage: the subject language decides the voices', () => {
  it("fetches the subject language's voices", async () => {
    const { urls } = mockApi()
    renderPage()
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    expect(urls).toEqual(['/api/voice/voices?language=pl'])
    await chooseSubject('History')
    await waitFor(() => expect(urls).toContain('/api/voice/voices?language=en'))
    await waitFor(() => expect(optionIds(voiceSelect())).toEqual(['', 'en_GB-cori-high']))
  })

  it('keeps the Polish defaults', async () => {
    mockApi()
    renderPage()
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    expect(voiceSelect()).toHaveValue('pl_PL-bass-high')
    expect(screen.getByLabelText('Rate')).toHaveValue(1)
  })

  it("defaults another language to its first installed Piper voice, else the browser voice, else its row", async () => {
    mockApi()
    const first = renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    expect(voiceSelect()).toHaveValue('en_GB-cori-high')
    first.unmount()
    sessionStorage.clear()

    server.resetHandlers()
    mockApi({ english: [voice('piper', 'en_GB-cori-high', 'Cori', false), BROWSER] })
    const second = renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('browser'))
    second.unmount()
    sessionStorage.clear()

    server.resetHandlers()
    mockApi({
      languages: languagesWith('en').map((row) =>
        row.code === 'en' ? { ...row, voice_provider: 'openai', voice_name: 'cedar' } : row,
      ),
    })
    renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('openai'))
    expect(voiceSelect()).toHaveValue('cedar')
  })

  it('remembers the choice per language', async () => {
    mockApi()
    renderPage()
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    await userEvent.selectOptions(audio(), 'openai')
    await userEvent.selectOptions(voiceSelect(), 'cedar')
    await chooseSubject('History')
    await waitFor(() => expect(voiceSelect()).toHaveValue('en_GB-cori-high'))
    expect(audio()).toHaveValue('piper')
    await userEvent.selectOptions(audio(), 'browser')
    await chooseSubject('Historia')
    await waitFor(() => expect(audio()).toHaveValue('openai'))
    expect(voiceSelect()).toHaveValue('cedar')
    expect(JSON.parse(sessionStorage.getItem('aoc.practiceVoice') ?? '{}')).toEqual({
      pl: { audio: 'openai', voice: 'cedar', rate: 1 },
      en: { audio: 'browser', voice: '', rate: 1 },
    })
  })

  it('does not race on a subject change', async () => {
    let release: () => void = () => {}
    const hold = { promise: new Promise<void>((resolve) => (release = resolve)) }
    mockApi({ hold })
    renderPage()
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    await chooseSubject('History')
    expect(audio()).toHaveValue('piper')
    expect(optionIds(voiceSelect())).toEqual([''])
    expect(voiceSelect()).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
    release()
    await waitFor(() => expect(voiceSelect()).toHaveValue('en_GB-cori-high'))
    expect(audio()).toHaveValue('piper')
    expect(optionIds(voiceSelect())).toEqual(['', 'en_GB-cori-high'])
  })

  it('restores a stored form on arrival, but only for the same language', async () => {
    mockApi()
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ subjectId: 1, audio: 'off', voice: '', rate: 1 }))
    const first = renderPage()
    await screen.findByLabelText('Subject')
    await waitFor(() => expect(audio()).toHaveValue('off'))
    first.unmount()

    sessionStorage.clear()
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ subjectId: 1, audio: 'off', voice: '', rate: 1 }))
    renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    expect(voiceSelect()).toHaveValue('en_GB-cori-high')
  })

  it('drops a remembered voice that is not in the language list', async () => {
    mockApi()
    sessionStorage.setItem(
      'aoc.practiceVoice',
      JSON.stringify({ en: { audio: 'piper', voice: 'pl_PL-bass-high', rate: 1 } }),
    )
    renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('piper'))
    expect(voiceSelect()).toHaveValue('')
  })

  it("sends the language's choice with the round", async () => {
    const { world } = mockApi()
    renderPage('/practice?subject=2')
    await waitFor(() => expect(voiceSelect()).toHaveValue('en_GB-cori-high'))
    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect((post?.body as { config: { question_audio: unknown } }).config.question_audio).toEqual({
      mode: 'premium',
      provider: 'piper',
      voice: 'en_GB-cori-high',
      rate: 1,
    })
  })

  it('greys out engines without a ready voice', async () => {
    mockApi({ english: [voice('piper', 'en_GB-cori-high', 'Cori', false), BROWSER] })
    renderPage('/practice?subject=2')
    await waitFor(() => expect(audio()).toHaveValue('browser'))
    const option = (name: string) => within(audio()).getByRole('option', { name }) as HTMLOptionElement
    expect(option('Piper voice (local)').disabled).toBe(true)
    expect(option('Azure voice').disabled).toBe(true)
    expect(option('OpenAI Realtime voice').disabled).toBe(true)
    expect(option('Browser voice').disabled).toBe(false)
    expect(option('Off (text only)').disabled).toBe(false)
    const line = screen.getByText(/No premium voice is ready for English/)
    expect(line).toHaveTextContent('No premium voice is ready for English. Add one in Settings → Languages.')
    expect(within(line).getByRole('link')).toHaveAttribute('href', '/settings#languages')
  })

  it('previews in the language', async () => {
    const { previews } = mockApi()
    vi.stubGlobal(
      'Audio',
      class {
        play() {
          return Promise.resolve()
        }
      },
    )
    renderPage('/practice?subject=2')
    await waitFor(() => expect(voiceSelect()).toHaveValue('en_GB-cori-high'))
    await userEvent.click(screen.getByRole('button', { name: 'Preview voice' }))
    await waitFor(() => expect(previews).toHaveLength(1))
    expect(previews[0]).toMatchObject({ provider: 'piper', voice: 'en_GB-cori-high', language: 'en' })

    const speak = vi.fn()
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        text: string
        lang = ''
        rate = 1
        voice: unknown = null
        constructor(text: string) {
          this.text = text
        }
      },
    )
    vi.stubGlobal('speechSynthesis', { getVoices: () => [{ lang: 'en-GB' }], speak, cancel: vi.fn() })
    await userEvent.selectOptions(audio(), 'browser')
    await userEvent.click(screen.getByRole('button', { name: 'Preview voice' }))
    const utterance = speak.mock.calls[0][0] as { lang: string; text: string }
    expect(utterance.lang).toBe('en-GB')
    expect(utterance.text).toMatch(/^Discuss the causes/)
  })

  it('marks topic titles with the content language and names with none', async () => {
    mockApi()
    renderPage('/practice?subject=2')
    await waitFor(() => expect(voiceSelect()).toBeInTheDocument())
    await userEvent.click(screen.getByRole('radio', { name: 'Selected topics' }))
    expect(await screen.findByText('Athenian democracy')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByLabelText('Subject')).not.toHaveAttribute('lang')
    for (const option of within(screen.getByLabelText('Subject')).getAllByRole('option')) {
      expect(option).not.toHaveAttribute('lang')
    }
    await userEvent.click(screen.getByRole('radio', { name: 'Selected documents' }))
    expect(await screen.findByText(/Ateny/)).not.toHaveAttribute('lang')

    await chooseSubject('Historia')
    await userEvent.click(screen.getByRole('radio', { name: 'Selected topics' }))
    const title = await screen.findByText(/Reformy/)
    expect(title).toHaveAttribute('lang', 'pl-PL')
    expect(title.textContent).toBe('Reformy a w Atenach')
  })
})
