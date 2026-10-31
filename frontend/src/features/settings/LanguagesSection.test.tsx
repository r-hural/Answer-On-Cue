import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { LANGUAGES, languagesWith } from '../library/languages.mocks'
import type { Preferences } from './api'
import { LanguagesSection } from './LanguagesSection'

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

interface Seen {
  languagePuts: { code: string; body: unknown }[]
  settingsPuts: unknown[]
  voiceUrls: string[]
}

function backend(rows = LANGUAGES, languagePut?: () => Response): Seen {
  const seen: Seen = { languagePuts: [], settingsPuts: [], voiceUrls: [] }
  let current = rows
  server.use(
    http.get('/api/languages', () => HttpResponse.json(current)),
    http.put('/api/languages/:code', async ({ request, params }) => {
      const body = (await request.json()) as object
      seen.languagePuts.push({ code: String(params.code), body })
      if (languagePut) return languagePut()
      current = current.map((row) => (row.code === params.code ? { ...row, ...body } : row))
      return HttpResponse.json(current.find((row) => row.code === params.code))
    }),
    http.put('/api/settings', async ({ request }) => {
      const body = (await request.json()) as object
      seen.settingsPuts.push(body)
      return HttpResponse.json({ ...preferences, ...body })
    }),
    http.get('/api/voice/voices', ({ request }) => {
      const url = new URL(request.url)
      seen.voiceUrls.push(url.pathname + url.search)
      return HttpResponse.json([])
    }),
  )
  return seen
}

function show() {
  return render(
    <LanguagesSection preferences={preferences} keysVersion={0} onPreferences={vi.fn()} />,
  )
}

describe('LanguagesSection', () => {
  it('lists the seven languages', async () => {
    backend()
    show()
    expect(screen.getByRole('heading', { level: 2, name: 'Languages' })).toBeInTheDocument()
    const list = await screen.findByRole('list', { name: 'Content languages' })
    await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(7))
    const items = within(list).getAllByRole('listitem')
    expect(items.map((item) => within(item).getByRole('strong').textContent)).toEqual([
      'Polish',
      'English',
      'German',
      'French',
      'Spanish',
      'Italian',
      'Ukrainian',
    ])
    expect(within(items[0]).getByText('polski')).toBeInTheDocument()
    expect(within(items[1]).queryByText('English', { selector: '.muted' })).toBeNull()
    expect(screen.getByRole('checkbox', { name: 'Enable Polish' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Enable English' })).not.toBeChecked()
    expect(within(items[0]).getByRole('radio', { name: 'Default' })).toBeInTheDocument()
    expect(within(items[1]).queryByRole('radio')).toBeNull()
  })

  it('enables a language', async () => {
    const seen = backend()
    show()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Enable English' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Enable English' })).toBeChecked())
    expect(seen.languagePuts).toEqual([{ code: 'en', body: { enabled: true } }])
    await waitFor(() => expect(seen.voiceUrls).toContain('/api/voice/voices?language=en'))
  })

  it('keeps a used language enabled', async () => {
    backend(LANGUAGES.map((row) => (row.code === 'pl' ? { ...row, subject_count: 3 } : row)))
    show()
    expect(await screen.findByText('Used by 3 subjects')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Enable Polish' })).toBeDisabled()
    expect(screen.getByText('Subjects use this language, so it stays enabled.')).toBeInTheDocument()
  })

  it('shows a refusal', async () => {
    backend(
      languagesWith('en'),
      () =>
        HttpResponse.json({ detail: 'At least one language must stay enabled.' }, { status: 409 }),
    )
    show()
    const box = await screen.findByRole('checkbox', { name: 'Enable English' })
    await waitFor(() => expect(box).toBeChecked())
    await userEvent.click(box)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'At least one language must stay enabled.',
    )
    expect(box).toBeChecked()
  })

  it('saves the rate alone', async () => {
    const seen = backend()
    show()
    const slider = screen.getByLabelText('Speech rate')
    fireEvent.change(slider, { target: { value: '1.2' } })
    await userEvent.click(screen.getByRole('button', { name: 'Save rate' }))
    expect(await screen.findByText('Speech rate saved.')).toBeInTheDocument()
    expect(seen.settingsPuts).toEqual([{ voice_rate: 1.2 }])
  })

  it('never sends the global voice', async () => {
    const seen = backend()
    show()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Enable English' }))
    const english = screen.getAllByRole('listitem')[1]
    await within(english).findByRole('radio', { name: 'Browser voice' })
    await userEvent.click(within(english).getByRole('radio', { name: 'Browser voice' }))
    await userEvent.click(within(english).getByRole('button', { name: 'Save voice' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save rate' }))
    await screen.findByText('Speech rate saved.')
    for (const body of seen.settingsPuts) {
      expect(body).not.toHaveProperty('voice_provider')
      expect(body).not.toHaveProperty('voice_name')
    }
  })
})
