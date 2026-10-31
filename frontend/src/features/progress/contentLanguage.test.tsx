import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { subjectHandler } from '../language/language.mocks'
import { CoveragePage } from './CoveragePage'
import { objectiveCoverage, setupCoverageWorld, subjectCoverage, topicCoverage, topicDetail, topicSource } from './coverage.mocks'
import { TopicCoveragePage } from './TopicCoveragePage'

describe('CoveragePage: content in the subject language', () => {
  it('sets objectives and topic links to en-GB for an English subject; selects stay plain', async () => {
    setupCoverageWorld({
      coverage: {
        3: subjectCoverage({
          topics: [
            topicCoverage({
              title: 'Athenian democracy',
              objectives: [objectiveCoverage({ text: 'Explain how ostracism worked' })],
            }),
          ],
        }),
      },
    })
    server.use(
      http.get('/api/subjects', () =>
        HttpResponse.json([
          { id: 3, name: 'History', description: null, document_count: 1, language: 'en' },
          { id: 4, name: 'Biologia', description: null, document_count: 1, language: 'pl' },
        ]),
      ),
    )
    render(
      <MemoryRouter initialEntries={['/progress/coverage?subject=3']}>
        <Routes>
          <Route path="/progress/coverage" element={<CoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )
    const objective = await screen.findByText('Explain how ostracism worked')
    expect(objective).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByRole('link', { name: 'Athenian democracy' })).toHaveAttribute('lang', 'en-GB')
    const select = screen.getByLabelText('Subject')
    expect(select).not.toHaveAttribute('lang')
    for (const option of select.querySelectorAll('option')) expect(option).not.toHaveAttribute('lang')
  })
})

describe('TopicCoveragePage: content in the subject language', () => {
  it("asks for the topic's subject and sets title, questions and excerpts to en-GB", async () => {
    setupCoverageWorld({
      topics: {
        7: topicDetail({
          title: 'Ostracism',
          subject_name: 'History',
          sources: [topicSource({ excerpt: 'A vote on pottery shards.' })],
        }),
      },
    })
    server.use(subjectHandler({ id: 3, name: 'History', language: 'en' }))
    render(
      <MemoryRouter initialEntries={['/progress/topics/7']}>
        <Routes>
          <Route path="/progress/topics/:topicId" element={<TopicCoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )
    const title = await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(title).toHaveAttribute('lang', 'en-GB'))
    expect(screen.getByText('A vote on pottery shards.')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByRole('link', { name: 'History' })).not.toHaveAttribute('lang')
  })
})
