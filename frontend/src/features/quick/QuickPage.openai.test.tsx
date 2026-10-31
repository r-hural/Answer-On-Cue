import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { job, topic } from '../library/library.mocks'
import { QuickPage } from './QuickPage'
import { preferences, setupQuick } from './quick.mocks'

beforeEach(() => {
  sessionStorage.clear()
})

describe('QuickPage with the OpenAI Realtime voice', () => {
  it('names the OpenAI Realtime voice as the question audio', async () => {
    setupQuick({
      topics: [
        topic({ id: 11, title: 'Budowa błony', question_count: 3 }),
        topic({ id: 12, title: 'Transport przez błonę', question_count: 3 }),
      ],
      activeQuestions: 6,
      jobs: [
        job({ id: 8, kind: 'questions.generate', status: 'done' }),
        job({ id: 7, kind: 'topics.extract', status: 'done', steps_done: 4 }),
      ],
      preferences: { ...preferences, voice_provider: 'openai', voice_name: 'marin' },
    })
    render(
      <MemoryRouter initialEntries={['/quick/5']}>
        <Routes>
          <Route path="/quick/:subjectId" element={<QuickPage />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('6 questions are ready')).toBeInTheDocument()
    const term = screen.getByText('Question audio')
    expect(term.nextElementSibling).toHaveTextContent('Read aloud (OpenAI Realtime voice)')
  })
})
