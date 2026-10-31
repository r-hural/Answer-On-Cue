import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProvidersStatus } from '../../api/providers'
import { appStrings } from '../../i18n/app'
import { server } from '../../test/msw'
import type { Preferences, VoiceInfo } from './api'
import { SettingsPage } from './SettingsPage'
import { storageGet } from './storage.mocks'

const preferences: Preferences = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gpt-6.1-sol',
  assessment_provider: 'openai',
  assessment_model: 'gpt-6.1-sol',
  voice_provider: 'piper',
  voice_name: 'pl_PL-gosia-medium',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
  round_question_count: 10,
  round_prep_seconds: 30,
  round_answer_seconds: 120,
}

function voices(installed: boolean): VoiceInfo[] {
  return [
    { provider: 'piper', id: 'pl_PL-gosia-medium', label: 'Gosia (Piper, local)', installed, gender: 'female' },
    { provider: 'azure_speech', id: 'pl-PL-ZofiaNeural', label: 'Zofia (Azure, neural)', installed: false, gender: 'female' },
    { provider: 'browser', id: 'browser', label: 'Browser voice (built into the system)', installed: true, gender: null },
  ]
}

function status(openai: boolean): ProvidersStatus {
  return {
    keys: [
      { provider: 'openai', configured: openai, masked: openai ? '••••abcd' : null, region: null },
      { provider: 'anthropic', configured: false, masked: null, region: null },
      { provider: 'azure_speech', configured: false, masked: null, region: null },
    ],
    roles: { generation: { provider: 'openai', model: 'gpt-6.1-sol' } },
  }
}

interface Backend {
  requests: { method: string; path: string; body: unknown }[]
}

function backend(options: { installed?: boolean; key?: boolean } = {}): Backend {
  const seen: Backend = { requests: [] }
  let installed = options.installed ?? true
  let hasKey = options.key ?? true
  const record = async (request: Request) => {
    const path = new URL(request.url).pathname
    const text = request.method === 'GET' ? '' : await request.text()
    seen.requests.push({ method: request.method, path, body: text ? JSON.parse(text) : undefined })
  }
  server.use(
    http.get('/api/providers/status', () => HttpResponse.json(status(hasKey))),
    http.get('/api/settings', () => HttpResponse.json(preferences)),
    http.get('/api/voice/voices', () => HttpResponse.json(voices(installed))),
    storageGet(),
    http.put('/api/settings', async ({ request }) => {
      await record(request.clone())
      return HttpResponse.json({ ...preferences, ...(await request.json()) as object })
    }),
    http.put('/api/providers/keys/:provider', async ({ request }) => {
      await record(request.clone())
      hasKey = true
      return HttpResponse.json(status(true))
    }),
    http.delete('/api/providers/keys/:provider', async ({ request }) => {
      await record(request.clone())
      hasKey = false
      return HttpResponse.json(status(false))
    }),
    http.post('/api/providers/keys/:provider/test', async ({ request }) => {
      await record(request.clone())
      return HttpResponse.json({ ok: false, error_kind: 'auth', message: 'The provider rejected the key.' })
    }),
    http.post('/api/voice/preview', async ({ request }) => {
      await record(request.clone())
      return HttpResponse.json({ audio_url: '/api/voice/cache/abc.wav' })
    }),
  )
  return Object.assign(seen, {
    install: () => {
      installed = true
    },
  })
}

afterEach(() => vi.restoreAllMocks())

describe('SettingsPage', () => {
  it('shows the privacy notice', async () => {
    backend()
    render(<SettingsPage />)
    expect(await screen.findByText(appStrings.privacy.title)).toBeInTheDocument()
    expect(screen.getByText(appStrings.privacy.body)).toBeInTheDocument()
  })

  it('shows the masked key and clears the input after saving', async () => {
    const seen = backend({ key: false })
    render(<SettingsPage />)
    const card = await screen.findByRole('listitem', { name: 'OpenAI' })
    expect(within(card).getByText('No key')).toBeInTheDocument()
    const input = within(card).getByLabelText(/API key OpenAI/)
    await userEvent.type(input, 'test-key-0000abcd')
    await userEvent.click(within(card).getByRole('button', { name: 'Save' }))
    expect(await within(card).findByText('Saved: ••••abcd')).toBeInTheDocument()
    expect(input).toHaveValue('')
    expect(seen.requests[0]).toMatchObject({ method: 'PUT', path: '/api/providers/keys/openai' })
    expect(document.body.textContent).not.toContain('test-key-0000abcd')
  })

  it('shows the backend message after a key test', async () => {
    backend()
    render(<SettingsPage />)
    const card = await screen.findByRole('listitem', { name: 'OpenAI' })
    await userEvent.click(within(card).getByRole('button', { name: 'Test' }))
    expect(await screen.findByText('The provider rejected the key.')).toBeInTheDocument()
  })

  it('asks before removing a key', async () => {
    const seen = backend()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    render(<SettingsPage />)
    const card = await screen.findByRole('listitem', { name: 'OpenAI' })
    await userEvent.click(within(card).getByRole('button', { name: 'Remove' }))
    expect(confirm).toHaveBeenCalledWith('Remove the OpenAI key?')
    expect(seen.requests).toEqual([])
    await userEvent.click(within(card).getByRole('button', { name: 'Remove' }))
    expect(await within(card).findByText('No key')).toBeInTheDocument()
    expect(seen.requests[0]).toMatchObject({ method: 'DELETE', path: '/api/providers/keys/openai' })
  })

  it('saves only the model fields', async () => {
    const seen = backend()
    render(<SettingsPage />)
    const field = await screen.findByLabelText('Model (Transcription (OpenAI))')
    await userEvent.clear(field)
    await userEvent.type(field, 'gpt-new')
    await userEvent.selectOptions(screen.getByLabelText('Provider (Answer assessment)'), 'anthropic')
    await userEvent.click(screen.getByRole('button', { name: 'Save models' }))
    expect(await screen.findByText('Models saved.')).toBeInTheDocument()
    const body = seen.requests.find((r) => r.method === 'PUT' && r.path === '/api/settings')!.body
    expect(Object.keys(body as object).sort()).toEqual([
      'assessment_model',
      'assessment_provider',
      'generation_model',
      'generation_provider',
      'transcription_model',
    ])
    expect(body).toMatchObject({ transcription_model: 'gpt-new', assessment_provider: 'anthropic' })
    expect(document.querySelector('datalist#model-suggestions option[value="gpt-6.1-sol"]')).not.toBeNull()
  })

  it('computes Completeness from Correctness and lists the sections', async () => {
    backend()
    render(<SettingsPage />)
    const correctness = await screen.findByLabelText('Correctness')
    const completeness = screen.getByLabelText('Completeness')
    expect(completeness).toHaveValue(50)
    await userEvent.clear(correctness)
    await userEvent.type(correctness, '35')
    expect(completeness).toHaveValue(45)
    expect(completeness).toHaveAttribute('readonly')
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(within(nav).getByRole('link', { name: 'Scoring' })).toHaveAttribute('href', '#scoring')
    expect(within(nav).getByRole('link', { name: 'Timing and targets' })).toHaveAttribute(
      'href',
      '#targets',
    )
    expect(screen.getByText(/finished rounds keep the values they used/)).toBeInTheDocument()
  })

  it('shows the band table with the stored bounds', async () => {
    backend()
    render(<SettingsPage />)
    const scoring = await screen.findByRole('region', { name: 'Scoring' })
    expect(within(scoring).getByText('Weak')).toBeInTheDocument()
    expect(within(scoring).getByText('0')).toBeInTheDocument()
    expect(within(scoring).getByLabelText('Needs improvement from')).toHaveValue(30)
    expect(within(scoring).getByLabelText('Good from')).toHaveValue(60)
    expect(within(scoring).getByLabelText('Very good from')).toHaveValue(80)
    expect(within(scoring).getByText('Counts towards Strong')).toBeInTheDocument()
  })

  it('saves the weights and the band bounds together', async () => {
    const seen = backend()
    render(<SettingsPage />)
    const very = await screen.findByLabelText('Very good from')
    await userEvent.clear(very)
    await userEvent.type(very, '75')
    await userEvent.click(screen.getByRole('button', { name: 'Save scoring' }))
    expect(await screen.findByText('Scoring saved.')).toBeInTheDocument()
    const put = seen.requests.find((r) => r.method === 'PUT' && r.path === '/api/settings')!
    expect(put.body).toEqual({ factual_weights: [30, 50], factual_band_bounds: [30, 60, 75] })
  })

  it('shows the defaults for new rounds and saves exactly those three', async () => {
    const seen = backend()
    render(<SettingsPage />)
    const timing = await screen.findByRole('region', { name: 'Timing and targets' })
    expect(within(timing).getByLabelText('Main questions')).toHaveValue(10)
    expect(within(timing).getByLabelText('Preparation (s)')).toHaveValue(30)
    expect(within(timing).getByLabelText('Answer time (s)')).toHaveValue(120)
    expect(within(timing).getByText('0 = answer at once')).toBeInTheDocument()
    const prep = within(timing).getByLabelText('Preparation (s)')
    await userEvent.clear(prep)
    await userEvent.type(prep, '15')
    await userEvent.click(within(timing).getByRole('button', { name: 'Save' }))
    expect(await within(timing).findByText('Defaults saved.')).toBeInTheDocument()
    const put = seen.requests.find((r) => r.method === 'PUT' && r.path === '/api/settings')!
    expect(put.body).toEqual({
      round_question_count: 10,
      round_prep_seconds: 15,
      round_answer_seconds: 120,
    })
  })

  it('shows the server text when the defaults are refused', async () => {
    backend()
    server.use(
      http.put('/api/settings', () =>
        HttpResponse.json({ detail: 'Input should be less than or equal to 30' }, { status: 422 }),
      ),
    )
    render(<SettingsPage />)
    const timing = await screen.findByRole('region', { name: 'Timing and targets' })
    await userEvent.click(within(timing).getByRole('button', { name: 'Save' }))
    expect(await within(timing).findByRole('alert')).toHaveTextContent('less than or equal to 30')
  })

  it('shows the weights error and changes nothing else', async () => {
    const seen = backend()
    server.use(
      http.put('/api/settings', () =>
        HttpResponse.json(
          { detail: 'Correctness and completeness weights must each be 10–70 and sum to 80.' },
          { status: 422 },
        ),
      ),
    )
    render(<SettingsPage />)
    const correctness = await screen.findByLabelText('Correctness')
    await userEvent.clear(correctness)
    await userEvent.type(correctness, '5')
    await userEvent.click(screen.getByRole('button', { name: 'Save scoring' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Correctness and completeness weights must each be 10–70 and sum to 80.',
    )
    expect(screen.getByLabelText('Needs improvement from')).toHaveValue(30)
    expect(screen.getByLabelText('Model (Question generation)')).toHaveValue('gpt-6.1-sol')
    expect(seen.requests).toEqual([])
  })

  it('lists Spending between Storage and backup and Privacy, with its section', async () => {
    backend()
    render(<SettingsPage />)
    const nav = await screen.findByRole('navigation', { name: 'Sections' })
    const spending = within(nav).getByRole('link', { name: 'Spending' })
    expect(spending).toHaveAttribute('href', '#spending')
    expect(await screen.findByRole('heading', { level: 2, name: 'Spending' })).toBeInTheDocument()
  })

  it('lists Storage and backup between Timing and targets and Privacy', async () => {
    backend()
    render(<SettingsPage />)
    const nav = await screen.findByRole('navigation', { name: 'Sections' })
    const links = within(nav).getAllByRole('link')
    const names = links.map((link) => link.textContent)
    expect(names.slice(-4)).toEqual(['Timing and targets', 'Storage and backup', 'Spending', 'Privacy'])
    expect(links[names.indexOf('Storage and backup')]).toHaveAttribute('href', '#storage')
    expect(await screen.findByRole('heading', { level: 2, name: 'Storage and backup' })).toBeInTheDocument()
  })

  it('reaches every enabled control with the keyboard', async () => {
    backend()
    render(<SettingsPage />)
    await screen.findByRole('listitem', { name: 'OpenAI' })
    await screen.findByRole('button', { name: 'Make a backup' }) // the storage section loads on its own
    await screen.findByRole('button', { name: 'Check for more voices' }) // so does Languages
    const controls = new Set(
      screen
        .getAllByRole('button')
        .concat(
          screen.getAllByRole('radio', { checked: true }), // arrow keys move within a radio group
          screen.getAllByRole('checkbox'),
          screen.getAllByRole('textbox'),
          screen.getAllByRole('combobox'),
          screen.getAllByRole('spinbutton'),
          screen.getAllByRole('slider'),
        )
        .filter((element) => !element.hasAttribute('disabled') && !element.hasAttribute('readonly')),
    )
    const reached = new Set<Element>()
    // The links (the side navigation comes first in the tab order) are tab stops too.
    const links = screen.getAllByRole('link').length
    for (let i = 0; i < controls.size + links + 4; i++) {
      await userEvent.tab()
      if (document.activeElement) reached.add(document.activeElement)
    }
    for (const control of controls) expect(reached.has(control)).toBe(true)
  })
})
