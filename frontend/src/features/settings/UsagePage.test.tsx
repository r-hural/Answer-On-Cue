import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { UsageRow, UsageTable } from '../costs/api'
import { summaryWithCeiling } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { UsagePage } from './UsagePage'

function row(over: Partial<UsageRow>): UsageRow {
  return {
    key: 'attempt.transcribe',
    label: null,
    provider: 'openai',
    model: 'gpt-transcribe',
    price_known: true,
    requests: 58,
    blocked: 0,
    input_tokens: 0,
    output_tokens: 0,
    audio_minutes: 104,
    characters: 0,
    estimated_usd: 0.47,
    reported_usd: 0.47,
    ...over,
  }
}

const tables: Record<string, UsageTable> = {
  operation: {
    month: '2026-10',
    group: 'operation',
    rows: [
      row({}),
      row({
        key: 'attempt.assess',
        provider: 'anthropic',
        model: 'claude-x',
        price_known: false,
        requests: 58,
        blocked: 2,
        input_tokens: 612000,
        output_tokens: 84000,
        audio_minutes: 0,
        estimated_usd: 1.21,
        reported_usd: null,
      }),
    ],
  },
  subject: {
    month: '2026-10',
    group: 'subject',
    rows: [
      row({ key: '3', label: 'Historia Polski', provider: null, model: null }),
      row({ key: 'unlinked', provider: null, model: null, reported_usd: 0.12 }),
    ],
  },
}

function Where() {
  const location = useLocation()
  return <p data-testid="where">{location.search}</p>
}

function show(path = '/settings/usage') {
  const requests: string[] = []
  server.use(
    http.get('/api/costs/summary', () => HttpResponse.json(summaryWithCeiling)),
    http.get('/api/costs/usage', ({ request }) => {
      const url = new URL(request.url)
      requests.push(url.search)
      return HttpResponse.json(tables[url.searchParams.get('group') ?? 'operation'] ?? tables.operation)
    }),
  )
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/settings/usage"
          element={
            <>
              <UsagePage />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
  return requests
}

describe('the Usage page', () => {
  it('follows usage.html: crumbs, heading, month, score boxes, tabs and the table', async () => {
    show()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')
    expect(within(crumbs).getByRole('link', { name: 'Spending' })).toHaveAttribute('href', '/settings#spending')
    expect(screen.getByRole('heading', { level: 1, name: 'AI usage' })).toBeInTheDocument()
    expect(screen.getByText(/Your provider's bill is the final word\./)).toBeInTheDocument()
    const month = await screen.findByLabelText('Month')
    expect(within(month).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'October 2026',
      'September 2026',
    ])
    expect(screen.getByText('This month')).toHaveClass('k')
    expect(screen.getByText('of the $15.00 ceiling')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '16% of the monthly ceiling used' })).toBeInTheDocument()
    expect(screen.getByText('about $0.31 per round')).toBeInTheDocument()
    expect(screen.getByText('$0.54')).toBeInTheDocument()
    expect(screen.getByText('Piper (local)')).toBeInTheDocument()

    const tabs = screen.getByRole('navigation', { name: 'Group by' })
    expect(within(tabs).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'By operation',
      'By round',
      'By subject',
      'By month',
    ])
    expect(within(tabs).getByRole('link', { name: 'By operation' })).toHaveAttribute('aria-current', 'page')
    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Operation',
      'Model',
      'Requests',
      'Amount',
      'Estimated',
      'Spend (estimated where the provider reported none)',
    ])
    const [, transcription, assessment] = within(table).getAllByRole('row')
    expect(within(transcription).getAllByRole('cell').map((c) => c.textContent)).toEqual([
      'Transcription',
      'OpenAI · gpt-transcribe',
      '58',
      '104 min audio',
      '$0.47',
      '$0.47',
    ])
    expect(assessment).toHaveTextContent('Answer assessment')
    expect(assessment).toHaveTextContent('price not known (estimated high)')
    expect(assessment).toHaveTextContent('2 blocked by the ceiling')
    expect(assessment).toHaveTextContent('612k in / 84k out tokens')
    expect(within(assessment).getByLabelText('Not reported')).toHaveTextContent('—')
  })

  it('keeps the chosen group in ?group= and shows subjects in Polish', async () => {
    const requests = show()
    await screen.findByRole('table')
    await userEvent.setup().click(screen.getByRole('link', { name: 'By subject' }))
    expect(await screen.findByText('Historia Polski')).not.toHaveAttribute('lang')
    expect(screen.getByText('Historia Polski')).toHaveClass('content')
    expect(screen.getByText('Not linked (deleted subject or before a restore)')).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('?group=subject')
    expect(screen.getByRole('link', { name: 'By subject' })).toHaveAttribute('aria-current', 'page')
    expect(requests).toEqual(['?group=operation', '?group=subject'])
  })

  it('lists the prices under "How these numbers are made"', async () => {
    show()
    expect(await screen.findByText('OpenAI gpt-transcribe: $0.0045 per audio minute')).toBeInTheDocument()
    expect(screen.getByText('OpenAI gpt-6.1-sol: $2.00 / $10.00 per million input / output tokens')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'How these numbers are made' })).toBeInTheDocument()
    expect(screen.getByText('Prices checked 2026-10-20.')).toBeInTheDocument()
  })

  it('shows an error notice when the usage cannot be loaded', async () => {
    show()
    server.use(http.get('/api/costs/usage', () => HttpResponse.json({ detail: 'x' }, { status: 500 })))
    await userEvent.setup().click(await screen.findByRole('link', { name: 'By round' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Usage could not be loaded.')
  })
})
