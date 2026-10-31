import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { chapteredWorld, doc, setupWorld, topic, version, type World } from './library.mocks'
import { SubjectPage } from './SubjectPage'

function renderPage(entry = '/library/1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/library/:subjectId" element={<SubjectPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const generated = (world: World) =>
  world.requests.filter((r) => r.method === 'POST' && r.path === '/api/questions/generate').map((r) => r.body)

async function generate(world: World, count = 1) {
  await userEvent.click(screen.getByRole('button', { name: 'Generate questions' }))
  await waitFor(() => expect(generated(world)).toHaveLength(count))
  return generated(world)[count - 1]
}

const base = { subject_id: 1, per_topic: 2, style: 'mixed', difficulty: 'mixed' }

describe('Generate for, two chapters', () => {
  it('preselects the book awaiting questions and sends its version and chapter', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=2')
    const select = await screen.findByLabelText('Generate for')
    await waitFor(() => expect(select).toHaveValue('book:30'))
    await screen.findByRole('option', { name: 'All topics (1)' })
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Topics from „Rewolucja” (1)',
      'All topics (1)',
    ])
    // the checkbox belongs to "All topics"
    expect(screen.queryByRole('checkbox', { name: /Only topics without questions/ })).toBeNull()
    expect(await generate(world)).toEqual({ ...base, document_version_id: 30, chapter_id: 2 })
  })

  it('All topics sends only the chapter when every topic has questions', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=1')
    const select = await screen.findByLabelText('Generate for')
    await screen.findByRole('option', { name: 'All topics (2)' })
    expect(select).toHaveValue('all')
    expect(screen.getByRole('option', { name: 'Topics from „Ateny” (2)' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Only topics without questions (0)' })).toBeDisabled()
    expect(await generate(world)).toEqual({ ...base, chapter_id: 1 })
  })

  it('Only topics without questions sends that chapter’s topic ids', async () => {
    const world = chapteredWorld()
    world.topics[1] = topic({ id: 6, title: 'Wojny perskie', chapter_id: 1, question_count: 0, question_counts: {} })
    renderPage('/library/1?chapter=1')
    expect(await screen.findByRole('checkbox', { name: 'Only topics without questions (1)' })).toBeChecked()
    expect(await generate(world)).toEqual({ ...base, chapter_id: 1, topic_ids: [6] })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Only topics without questions (1)' }))
    expect(await generate(world, 2)).toEqual({ ...base, chapter_id: 1 })
  })

  it('a book chosen by hand sends its version', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=1')
    await screen.findByRole('option', { name: 'All topics (2)' })
    await userEvent.selectOptions(screen.getByLabelText('Generate for'), 'book:10')
    expect(await generate(world)).toEqual({ ...base, document_version_id: 10, chapter_id: 1 })
  })

  it('"Generate questions" on a document row picks that book without starting anything', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=2')
    const select = await screen.findByLabelText('Generate for')
    await waitFor(() => expect(select).toHaveValue('book:30'))
    await screen.findByRole('option', { name: 'All topics (1)' })
    await userEvent.selectOptions(select, 'all')
    // only the book that has topics and no questions gets the link
    expect(screen.queryByRole('link', { name: 'Generate questions: Ateny' })).toBeNull()
    await userEvent.click(screen.getByRole('link', { name: 'Generate questions: Rewolucja' }))
    await waitFor(() => expect(screen.getByLabelText('Generate for')).toHaveValue('book:30'))
    expect(generated(world)).toHaveLength(0)
  })

  it('shows the message when another book is being generated', async () => {
    chapteredWorld()
    const detail = 'Questions are already being generated for this subject. Wait for that to finish.'
    server.use(http.post('/api/questions/generate', () => HttpResponse.json({ detail }, { status: 409 })))
    renderPage('/library/1?chapter=2')
    await waitFor(() => expect(screen.getByLabelText('Generate for')).toHaveValue('book:30'))
    await screen.findByRole('option', { name: 'All topics (1)' })
    await userEvent.click(screen.getByRole('button', { name: 'Generate questions' }))
    expect(await screen.findByText(detail)).toBeInTheDocument()
  })
})

describe('Generate for, one chapter', () => {
  it('offers the book but never sends a chapter', async () => {
    const world = setupWorld({
      documents: [
        doc({
          latest_version: version({ topics_extracted_at: '2026-10-24T10:00:00', questions_generated_at: null, topic_count: 1 }),
        }),
      ],
      topics: [topic({ question_count: 0, question_counts: {} })],
    })
    renderPage()
    const select = await screen.findByLabelText('Generate for')
    await waitFor(() => expect(select).toHaveValue('book:10'))
    await screen.findByRole('option', { name: 'All topics (1)' })
    expect(await generate(world)).toEqual({ ...base, document_version_id: 10 })
  })
})
