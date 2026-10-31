import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { summaryNoCeiling, summaryWithCeiling } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import type { Preferences } from './api'
import { SpendingSection } from './SpendingSection'

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
  spending_ceiling_usd: 15,
}

function show(prefs: Preferences = preferences, onSaved = vi.fn()) {
  const view = render(
    <MemoryRouter>
      <SpendingSection preferences={prefs} onSaved={onSaved} />
    </MemoryRouter>,
  )
  return { ...view, onSaved }
}

describe('Settings → Spending', () => {
  it('follows the template: field, hint, plain Save, the month box and the usage link', async () => {
    server.use(http.get('/api/costs/summary', () => HttpResponse.json(summaryWithCeiling)))
    const { container } = show()
    const section = container.querySelector('section#spending')
    expect(section).not.toBeNull()
    expect(screen.getByRole('heading', { level: 2, name: 'Spending' })).toBeInTheDocument()
    const field = screen.getByLabelText('Monthly ceiling for AI usage (USD)')
    expect(field).toHaveValue(15)
    expect(screen.getByText(/Checked before each new AI request\./)).toHaveClass('hint')
    expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('btn')
    expect(container.querySelector('.btn.primary')).toBeNull()
    expect(await screen.findByText('October so far (estimated)')).toHaveClass('k')
    expect(screen.getByText('$2.40', { exact: false }).closest('p')).toHaveTextContent('$2.40 of $15.00')
    const meter = screen.getByRole('img', { name: '16% of the monthly ceiling used' })
    expect(meter).toHaveClass('meter-bar')
    expect(meter.querySelector('span')).toHaveStyle({ width: '16%' })
    expect(screen.getByRole('link', { name: 'Usage by operation, round, subject and month' })).toHaveAttribute(
      'href',
      '/settings/usage',
    )
  })

  it('without a ceiling: an empty field, the spend and "No ceiling set", no meter', async () => {
    show({ ...preferences, spending_ceiling_usd: 0 })
    expect(screen.getByLabelText('Monthly ceiling for AI usage (USD)')).toHaveValue(null)
    expect(await screen.findByText('No ceiling set')).toBeInTheDocument()
    expect(screen.getByText('$2.40')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('saves the ceiling and reloads the month box; clearing sends 0', async () => {
    const bodies: unknown[] = []
    let summaries = 0
    server.use(
      http.get('/api/costs/summary', () => {
        summaries += 1
        return HttpResponse.json(summaries > 1 ? summaryWithCeiling : summaryNoCeiling)
      }),
      http.put('/api/settings', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>
        bodies.push(body)
        return HttpResponse.json({ ...preferences, ...body })
      }),
    )
    const { onSaved } = show({ ...preferences, spending_ceiling_usd: 0 })
    await screen.findByText('No ceiling set')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Monthly ceiling for AI usage (USD)'), '15')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Spending ceiling saved.')).toBeInTheDocument()
    expect(bodies).toEqual([{ spending_ceiling_usd: 15 }])
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ spending_ceiling_usd: 15 }))
    expect(await screen.findByRole('img', { name: '16% of the monthly ceiling used' })).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Monthly ceiling for AI usage (USD)'))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(bodies).toEqual([{ spending_ceiling_usd: 15 }, { spending_ceiling_usd: 0 }]))
  })

  it('refuses a negative or unreadable ceiling instead of saving it as "no ceiling"', async () => {
    const bodies: unknown[] = []
    server.use(
      http.put('/api/settings', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(preferences)
      }),
    )
    show()
    const user = userEvent.setup()
    const field = screen.getByLabelText('Monthly ceiling for AI usage (USD)')
    const invalid = 'Enter an amount of 0 or more (USD), or leave the field empty for no ceiling.'
    await user.clear(field)
    await user.type(field, '-5')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(invalid)
    // text the number field cannot parse reads as '' with validity.badInput
    Object.defineProperty(field, 'validity', { configurable: true, value: { badInput: true } })
    fireEvent.change(field, { target: { value: '' } })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(invalid)
    expect(bodies).toEqual([])
  })

  it("shows the server's message for an invalid value", async () => {
    server.use(
      http.put('/api/settings', () =>
        HttpResponse.json({ detail: 'Input should be less than or equal to 10000' }, { status: 422 }),
      ),
    )
    show()
    const user = userEvent.setup()
    const field = screen.getByLabelText('Monthly ceiling for AI usage (USD)')
    await user.clear(field)
    await user.type(field, '20000')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be less than or equal to 10000')
  })
})
