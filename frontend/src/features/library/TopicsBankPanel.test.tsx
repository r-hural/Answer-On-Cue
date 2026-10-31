import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { startGeneration, type Topic } from './api'
import { BankPanel, TopicsPanel } from './TopicsBankPanel'

function renderPanel(topics: Topic[] = []) {
  const bodies: unknown[] = []
  server.use(
    http.post('/api/questions/generate', async ({ request }) => {
      bodies.push(await request.json())
      return HttpResponse.json({ job_id: 1 }, { status: 202 })
    }),
  )
  render(
    <MemoryRouter>
      <BankPanel
        subjectId={3}
        topics={topics}
        hasTopics
        questionCount={0}
        canRun
        busy={false}
        onGenerate={(options) => void startGeneration(3, options)}
      />
    </MemoryRouter>,
  )
  return bodies
}

describe('BankPanel Sources', () => {
  it('offers both source policies, Only my materials by default', () => {
    renderPanel()
    const select = screen.getByLabelText('Sources')
    expect(select).toHaveValue('materials')
    expect(screen.getByRole('option', { name: 'Only my materials' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'My materials + expand' })).toBeInTheDocument()
  })

  it('posts source_policy "expanded" when Expand the topic is chosen', async () => {
    const bodies = renderPanel()
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Sources'), 'expanded')
    await user.click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed', source_policy: 'expanded' })
  })

  it('keeps the strict body unchanged by default', async () => {
    const bodies = renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed' })
  })
})

describe('TopicsPanel collapsed by default', () => {
  const topics = [
    { id: 5, title: 'Ustrój', description: null, origin: 'ai', status: 'active', objectives: [], passage_count: 3, question_count: 0 },
  ] as unknown as Parameters<typeof TopicsPanel>[0]['topics']
  const renderTopics = (subjectId = 3) =>
    render(
      <MemoryRouter>
        <TopicsPanel subjectId={subjectId} topics={topics} terms={[]} />
      </MemoryRouter>,
    )

  it('starts collapsed with the count and Edit topics; the toggle shows the list and is remembered', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    const view = renderTopics()
    expect(screen.getByText('1 topic')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Edit topics' })).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Show topics' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('list')).toBeNull()
    await user.click(toggle)
    expect(screen.getByRole('button', { name: 'Hide topics' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('list')).toBeVisible()
    expect(screen.getByText('Ustrój')).toBeInTheDocument()
    view.unmount()
    const again = renderTopics()
    expect(screen.getByRole('button', { name: 'Hide topics' })).toBeInTheDocument()
    again.unmount()
    renderTopics(4)
    expect(screen.getByRole('button', { name: 'Show topics' })).toBeInTheDocument()
  })

  it('works without storage', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    try {
      renderTopics()
      expect(screen.getByRole('button', { name: 'Show topics' })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })
})

describe('BankPanel Topics', () => {
  const topic = (id: number, materials: number, expanded = 0, children: Topic[] = []): Topic => ({
    id,
    title: `Temat ${id}`,
    description: null,
    origin: 'ai',
    status: 'active',
    objectives: [],
    passage_count: 2,
    question_count: materials + expanded,
    children,
    question_counts: { materials, expanded },
  })
  // the second book's topics (21, 22 and the child 23) have no questions yet
  const topics = [topic(11, 3), topic(12, 2, 1), topic(21, 0, 2, [topic(23, 0)]), topic(22, 0)]

  it('defaults to the topics without questions and sends only their ids', async () => {
    const bodies = renderPanel(topics)
    expect(screen.getByRole('checkbox', { name: 'Only topics without questions (3)' })).toBeChecked()
    expect(screen.getByRole('option', { name: 'All topics (5)' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed', topic_ids: [21, 23, 22] })
  })

  it('counts under the chosen Sources policy', async () => {
    const bodies = renderPanel(topics)
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Sources'), 'expanded')
    expect(screen.getByRole('checkbox', { name: 'Only topics without questions (3)' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ source_policy: 'expanded', topic_ids: [11, 23, 22] })
  })

  it('All topics sends no topic_ids', async () => {
    const bodies = renderPanel(topics)
    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Only topics without questions (3)' }))
    await user.click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed' })
  })

  it('defaults to All topics when every topic has questions', () => {
    renderPanel([topic(11, 3), topic(12, 1)])
    expect(screen.getByLabelText('Generate for')).toHaveValue('all')
    expect(screen.getByRole('checkbox', { name: 'Only topics without questions (0)' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Only topics without questions (0)' })).not.toBeChecked()
  })
})
