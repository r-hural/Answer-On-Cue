import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { PriceRow, UsageTable } from '../costs/api'
import { prices, summaryNoCeiling } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { UsagePage } from './UsagePage'

const realtime: PriceRow = {
  provider: 'openai',
  model: 'gpt-realtime-2.1-mini',
  role: 'voice',
  input_per_m: 0.6,
  output_per_m: 20,
  audio_per_min: 0,
  chars_per_m: 0,
  checked: '2026-10-20',
}

const table: UsageTable = {
  month: '2026-10',
  group: 'operation',
  rows: [
    {
      key: 'voice.verify',
      label: null,
      provider: 'openai',
      model: 'gpt-transcribe',
      price_known: true,
      requests: 4,
      blocked: 0,
      input_tokens: 0,
      output_tokens: 0,
      audio_minutes: 1.2,
      characters: 0,
      estimated_usd: 0.01,
      reported_usd: 0.01,
    },
  ],
}

function show() {
  server.use(
    http.get('/api/costs/summary', () =>
      HttpResponse.json({ ...summaryNoCeiling, voice_provider: 'openai', voice_usd: 0.08 }),
    ),
    http.get('/api/costs/usage', () => HttpResponse.json(table)),
    http.get('/api/costs/prices', () => HttpResponse.json([...prices, realtime])),
  )
  render(
    <MemoryRouter initialEntries={['/settings/usage']}>
      <UsagePage />
    </MemoryRouter>,
  )
}

describe('the Usage page with the OpenAI Realtime voice', () => {
  it('names the voice in the month box', async () => {
    show()
    expect(await screen.findByText('OpenAI Realtime voice')).toHaveClass('of')
  })

  it('prices the Realtime model per token', async () => {
    show()
    expect(
      await screen.findByText(
        'OpenAI gpt-realtime-2.1-mini: $0.60 / $20.00 per million input / output tokens',
      ),
    ).toBeInTheDocument()
  })

  it('labels the check operation', async () => {
    show()
    const table = await screen.findByRole('table')
    const [, row] = within(table).getAllByRole('row')
    expect(within(row).getAllByRole('cell')[0]).toHaveTextContent('Question voice check')
  })
})
