import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { generationEstimate, monthWithCeiling } from './costs.mocks'
import { EstimateLine } from './EstimateLine'

describe('EstimateLine', () => {
  it('states the generation estimate as a numeric hint', async () => {
    const { container } = render(<EstimateLine generation={{ subject_id: 1, per_topic: 5 }} />)
    const line = await screen.findByText('Estimate: 42 questions, about 4 minutes and $0.30 of AI usage.')
    expect(line.tagName).toBe('P')
    expect(line).toHaveClass('hint', 'num')
    expect(container.querySelector('.text-error')).toBeNull()
  })

  it('states the extraction estimate', async () => {
    render(<EstimateLine extraction={7} />)
    expect(await screen.findByText('Analysing topics: about $0.04 of AI usage.')).toBeInTheDocument()
  })

  it('adds what is left when the job does not fit', async () => {
    server.use(
      http.post('/api/costs/estimate/generation', () =>
        HttpResponse.json({
          ...generationEstimate,
          fits: false,
          month: { ...monthWithCeiling, spent_usd: 14.88, remaining_usd: 0.12 },
        }),
      ),
    )
    render(<EstimateLine generation={{ subject_id: 1, per_topic: 5 }} />)
    const left = await screen.findByText('Only $0.12 is left of your $15.00 monthly ceiling.')
    expect(left).toHaveClass('text-error')
    expect(left.closest('p')).toHaveTextContent(
      'Estimate: 42 questions, about 4 minutes and $0.30 of AI usage. Only $0.12 is left of your $15.00 monthly ceiling.',
    )
  })

  it('renders nothing when the estimate fails', async () => {
    let asked = 0
    server.use(
      http.post('/api/costs/estimate/generation', () => {
        asked += 1
        return HttpResponse.json({ detail: 'x' }, { status: 500 })
      }),
    )
    const { container } = render(<EstimateLine generation={{ subject_id: 1, per_topic: 5 }} />)
    await waitFor(() => expect(asked).toBe(1))
    expect(container).toBeEmptyDOMElement()
  })
})
