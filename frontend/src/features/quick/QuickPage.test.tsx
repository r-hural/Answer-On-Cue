import { http, HttpResponse } from 'msw'
import { generationEstimate } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { generationBody } from './api'
import { perTopic } from './quickState'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { job } from '../library/library.mocks'
import { intervals } from '../library/strings'
import { QuickPage } from './QuickPage'
import { QuickUploadPage } from './QuickUploadPage'
import { requestsTo, setupQuick, topics } from './quick.mocks'

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/quick" element={<QuickUploadPage />} />
        <Route path="/quick/:subjectId" element={<QuickPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

const original = intervals.jobs
beforeEach(() => {
  intervals.jobs = 20
  sessionStorage.clear()
})
afterEach(() => {
  intervals.jobs = original
})

const txt = (name = 'Biologia_komorki_rozdzial_3.txt') =>
  new File(['Treść pliku.'], name, { type: 'text/plain' })

describe('QuickUploadPage', () => {
  it('offers the primary upload only once a file is chosen', async () => {
    setupQuick()
    renderAt('/quick')
    const button = screen.getByRole('button', { name: 'Upload' })
    expect(button).toBeDisabled()
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    const ready = screen.getByRole('button', { name: 'Upload „Biologia_komorki_rozdzial_3.txt”' })
    expect(ready).toBeEnabled()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Cancel' })).toHaveAttribute('href', '/')
  })

  it('creates a subject named after the file, uploads and opens the next step', async () => {
    const world = setupQuick()
    renderAt('/quick')
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    await userEvent.click(screen.getByRole('button', { name: /^Upload „/ }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finding topics…' })).toBeInTheDocument())
    expect(requestsTo(world, 'POST', /^\/api\/subjects$/)[0].body).toEqual({
      name: 'Biologia komorki rozdzial 3',
      description: null,
      language: 'pl',
    })
    expect(requestsTo(world, 'POST', /\/subjects\/5\/documents$/)).toHaveLength(1)
  })

  it('retries a taken name with " (2)"', async () => {
    const world = setupQuick({ takenNames: ['Biologia komorki rozdzial 3'] })
    renderAt('/quick')
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    await userEvent.click(screen.getByRole('button', { name: /^Upload „/ }))
    await screen.findByRole('heading', { name: 'Finding topics…' })
    const names = requestsTo(world, 'POST', /^\/api\/subjects$/).map((r) => (r.body as { name: string }).name)
    expect(names).toEqual(['Biologia komorki rozdzial 3', 'Biologia komorki rozdzial 3 (2)'])
  })

  it('deletes the new subject and shows the message when the upload is refused', async () => {
    const world = setupQuick({ uploadStatus: 415 })
    renderAt('/quick')
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    await userEvent.click(screen.getByRole('button', { name: /^Upload „/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unsupported file type.')
    expect(requestsTo(world, 'DELETE', /\/subjects\/5$/)).toHaveLength(1)
    expect(screen.getByRole('button', { name: /^Upload „/ })).toBeEnabled()
  })
})

describe('QuickPage: finding topics', () => {
  it('starts the extraction once and shows its progress', async () => {
    const world = setupQuick({
      jobPolls: [job({ id: 7, status: 'running', steps_done: 1, steps_total: 4 })],
    })
    renderAt('/quick/5')
    const bar = await screen.findByRole('progressbar', { name: 'Topics found' })
    await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '25'))
    expect(requestsTo(world, 'POST', /topics\/extract$/)).toHaveLength(1)
    expect(requestsTo(world, 'POST', /topics\/extract$/)[0].body).toEqual({ document_version_id: 10 })
    expect(screen.getByText(/You can leave this page; the task continues\./)).toBeInTheDocument()
    expect(screen.getByText('„biologia.pdf”')).not.toHaveAttribute('lang')
  })

  it('does not start a second extraction when one already runs', async () => {
    const world = setupQuick({
      jobs: [job({ id: 7, status: 'running', steps_done: 2, steps_total: 4 })],
      jobPolls: [job({ id: 7, status: 'running', steps_done: 2, steps_total: 4 })],
    })
    renderAt('/quick/5')
    const bar = await screen.findByRole('progressbar')
    await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '50'))
    expect(requestsTo(world, 'POST', /topics\/extract$/)).toHaveLength(0)
  })

  it('moves on to the topics when the extraction ends', async () => {
    setupQuick({
      topics: topics(),
      jobs: [job({ id: 7, status: 'running' })],
      jobPolls: [job({ id: 7, status: 'done', steps_done: 4 })],
    })
    renderAt('/quick/5')
    expect(await screen.findByRole('heading', { name: /Topics found in/ })).toBeInTheDocument()
  })

  it('shows a failed extraction with a way back', async () => {
    setupQuick({
      jobs: [job({ id: 7, status: 'failed', error_message: 'The provider is unavailable.', resumable: false })],
    })
    renderAt('/quick/5')
    expect(await screen.findByRole('alert')).toHaveTextContent('The provider is unavailable.')
    expect(screen.getByRole('link', { name: 'Back' })).toHaveAttribute('href', '/quick')
  })
})

describe('QuickPage: topics', () => {
  const ready = () =>
    setupQuick({
      topics: topics(),
      terms: [{ term: 'fosfolipidy', definition: null, topic_id: 11 }],
      jobs: [job({ id: 7, status: 'done', steps_done: 4 })],
    })

  it('lists the topics, all ticked, with the file facts and terms', async () => {
    ready()
    renderAt('/quick/5')
    const heading = await screen.findByRole('heading', { name: /Topics found in/ })
    expect(heading).toHaveTextContent('„biologia.pdf”')
    expect(screen.getByText('24 pages · 31 200 characters · 96 passages')).toBeInTheDocument()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes).toHaveLength(3)
    boxes.forEach((box) => expect(box).toBeChecked())
    expect(screen.getByText('Budowa błony')).toHaveAttribute('lang', 'pl-PL')
    expect(screen.getByText('fosfolipidy')).toHaveAttribute('lang', 'pl-PL')
    expect(screen.getByLabelText('Questions')).toHaveValue(10)
    expect(screen.getByText('Uses your OpenAI key.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Edit topics' })).toHaveAttribute('href', '/library/5/topics')
    expect(screen.getByRole('link', { name: 'Check the extracted text' })).toHaveAttribute('href', '/library/5')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('prepares questions for the ticked topics only', async () => {
    const world = ready()
    renderAt('/quick/5')
    await screen.findByRole('heading', { name: /Topics found in/ })
    await userEvent.click(screen.getByRole('checkbox', { name: /Mitochondria/ }))
    const count = screen.getByLabelText('Questions')
    await userEvent.clear(count)
    await userEvent.type(count, '8')
    await userEvent.click(screen.getByRole('button', { name: 'Prepare questions' }))
    await waitFor(() => expect(requestsTo(world, 'POST', /questions\/generate$/)).toHaveLength(1))
    expect(requestsTo(world, 'POST', /questions\/generate$/)[0].body).toEqual({
      subject_id: 5,
      topic_ids: [11, 12],
      per_topic: 4,
      style: 'mixed',
      difficulty: 'mixed',
    })
  })

  it('cannot prepare an empty or out-of-range number of questions', async () => {
    const world = ready()
    renderAt('/quick/5')
    await screen.findByRole('heading', { name: /Topics found in/ })
    const count = screen.getByLabelText('Questions')
    const prepare = screen.getByRole('button', { name: 'Prepare questions' })
    const error = 'Enter a whole number from 1 to 30.'
    expect(screen.queryByText(error)).toBeNull()
    await userEvent.clear(count)
    expect(prepare).toBeDisabled()
    expect(screen.getByText(error)).toBeInTheDocument()
    expect(count).toHaveAttribute('aria-invalid', 'true')
    for (const wrong of ['0', '31']) {
      await userEvent.clear(count)
      await userEvent.type(count, wrong)
      expect(prepare).toBeDisabled()
    }
    await userEvent.clear(count)
    await userEvent.type(count, '30')
    expect(prepare).toBeEnabled()
    expect(screen.queryByText(error)).toBeNull()
    await userEvent.clear(count)
    await userEvent.type(count, '0')
    await userEvent.click(prepare)
    expect(requestsTo(world, 'POST', /questions\/generate$/)).toHaveLength(0)
  })

  it('cannot prepare questions with no topic ticked', async () => {
    ready()
    renderAt('/quick/5')
    await screen.findByRole('heading', { name: /Topics found in/ })
    for (const box of screen.getAllByRole('checkbox')) await userEvent.click(box)
    expect(screen.getByRole('button', { name: 'Prepare questions' })).toBeDisabled()
  })
})

describe('QuickPage: the estimated AI usage of the questions', () => {
  it('prices the quick generation body in the side box', async () => {
    const bodies: unknown[] = []
    setupQuick({ topics: topics(), jobs: [job({ id: 7, status: 'done', steps_done: 4 })] })
    server.use(
      http.post('/api/costs/estimate/generation', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ ...generationEstimate, usd: 0.06 })
      }),
    )
    renderAt('/quick/5')
    const strong = await screen.findByText('about $0.06')
    expect(strong.tagName).toBe('STRONG')
    expect(strong).toHaveClass('num')
    expect(strong.closest('p')).toHaveTextContent('Estimated AI usage: about $0.06. Uses your OpenAI key.')
    expect(bodies[bodies.length - 1]).toEqual(generationBody(5, [11, 12, 13], perTopic(10, 3)))
  })
})
