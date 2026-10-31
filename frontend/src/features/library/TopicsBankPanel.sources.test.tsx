import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { startGeneration } from './api'
import { BankPanel } from './TopicsBankPanel'

function renderPanel() {
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
        topics={[]}
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

describe('BankPanel: three generation sources', () => {
  it('offers Only my materials, My materials + expand and Only expand, with a hint', () => {
    renderPanel()
    const select = screen.getByLabelText('Sources') as HTMLSelectElement
    expect([...select.options].map((option) => option.text)).toEqual([
      'Only my materials',
      'My materials + expand',
      'Only expand',
    ])
    expect(select).toHaveValue('materials')
    expect(select).toHaveAccessibleDescription(
      'Only expand: questions that go beyond your files; your files only set the topic.',
    )
  })

  it('sends source_policy "outside" for Only expand and nothing for the default', async () => {
    const bodies = renderPanel()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed' })
    await user.selectOptions(screen.getByLabelText('Sources'), 'outside')
    await user.click(screen.getByRole('button', { name: 'Generate questions' }))
    await vi.waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]).toEqual({ subject_id: 3, per_topic: 2, style: 'mixed', difficulty: 'mixed', source_policy: 'outside' })
  })
})
