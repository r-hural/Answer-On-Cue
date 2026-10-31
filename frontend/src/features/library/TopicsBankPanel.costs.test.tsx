import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { generationEstimate, monthWithCeiling } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { startGeneration, type Document, type Topic } from './api'
import { BankPanel } from './TopicsBankPanel'

const topic = (id: number, questions: number): Topic => ({
  id,
  title: `Temat ${id}`,
  description: null,
  origin: 'ai',
  status: 'active',
  objectives: [],
  passage_count: 3,
  question_count: questions,
  question_counts: questions ? { materials: questions } : {},
})

const book: Document = {
  id: 1,
  title: 'Ateny',
  version_count: 1,
  latest_version: {
    id: 30,
    version: 1,
    filename: 'ateny.txt',
    status: 'imported',
    message: null,
    char_count: 4200,
    page_count: null,
    passage_count: 3,
    topics_extracted_at: '2026-10-20T10:00:00Z',
    topic_count: 2,
  },
}

function renderPanel({ topics = [topic(5, 2), topic(6, 0)], documents = [] as Document[], hasTopics = true } = {}) {
  const estimates: unknown[] = []
  const generated: unknown[] = []
  server.use(
    http.post('/api/costs/estimate/generation', async ({ request }) => {
      estimates.push(await request.json())
      return HttpResponse.json(generationEstimate)
    }),
    http.post('/api/questions/generate', async ({ request }) => {
      generated.push(await request.json())
      return HttpResponse.json({ job_id: 1 }, { status: 202 })
    }),
  )
  const view = render(
    <MemoryRouter>
      <BankPanel
        subjectId={3}
        topics={topics}
        hasTopics={hasTopics}
        questionCount={2}
        canRun
        busy={false}
        documents={documents}
        onGenerate={(options) => void startGeneration(3, options)}
      />
    </MemoryRouter>,
  )
  return { estimates, generated, view }
}

const LINE = 'Estimate: 42 questions, about 4 minutes and $0.30 of AI usage.'

async function sameBodies(estimates: unknown[], generated: unknown[]) {
  const before = generated.length
  await userEvent.setup().click(screen.getByRole('button', { name: 'Generate questions' }))
  await waitFor(() => expect(generated).toHaveLength(before + 1))
  expect(estimates[estimates.length - 1]).toEqual(generated[generated.length - 1])
}

describe('BankPanel: the estimate before generating', () => {
  it('sits after the needs-topics hint and before the Generate button', async () => {
    const first = renderPanel({ hasTopics: false, topics: [] })
    expect(screen.queryByText(LINE)).toBeNull() // nothing to generate yet
    first.view.unmount()
    const { view } = renderPanel()
    const line = await screen.findByText(LINE)
    const button = screen.getByRole('button', { name: 'Generate questions' })
    expect(line.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(line.nextElementSibling).toBe(button.closest('.actions'))
    view.unmount()
  })

  it('prices exactly the body the Generate button sends, and follows every field', async () => {
    const { estimates, generated } = renderPanel()
    await screen.findByText(LINE)
    // all topics, only those without questions (the default when there are such topics)
    await sameBodies(estimates, generated)
    expect(generated[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed', topic_ids: [6] })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: /Only topics without questions/ }))
    await waitFor(() => expect((estimates[estimates.length - 1] as { topic_ids?: number[] }).topic_ids).toBeUndefined())
    await sameBodies(estimates, generated)

    await user.selectOptions(screen.getByLabelText('Sources'), 'expanded')
    await waitFor(() => expect(estimates[estimates.length - 1]).toMatchObject({ source_policy: 'expanded' }))
    await sameBodies(estimates, generated)

    const per = screen.getByLabelText(/Questions per topic/)
    await user.clear(per)
    await user.type(per, '7')
    await waitFor(() => expect(estimates[estimates.length - 1]).toMatchObject({ per_topic: 7 }))
    await sameBodies(estimates, generated)
  })

  it('prices one book with its version', async () => {
    const { estimates, generated } = renderPanel({ documents: [book] })
    await screen.findByText(LINE)
    await userEvent.setup().selectOptions(screen.getByLabelText('Generate for'), 'book:30')
    await waitFor(() => expect(estimates[estimates.length - 1]).toMatchObject({ document_version_id: 30 }))
    expect(estimates[estimates.length - 1]).toMatchObject({ document_version_id: 30 })
    await sameBodies(estimates, generated)
  })

  it('leaves Generate enabled when the job does not fit: the server decides', async () => {
    server.use(
      http.post('/api/costs/estimate/generation', () =>
        HttpResponse.json({ ...generationEstimate, fits: false, month: { ...monthWithCeiling, remaining_usd: 0.12 } }),
      ),
    )
    render(
      <MemoryRouter>
        <BankPanel subjectId={3} topics={[topic(5, 0)]} hasTopics questionCount={0} canRun busy={false} onGenerate={() => {}} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('Only $0.12 is left of your $15.00 monthly ceiling.')).toHaveClass('text-error')
    expect(screen.getByRole('button', { name: 'Generate questions' })).toBeEnabled()
  })
})
