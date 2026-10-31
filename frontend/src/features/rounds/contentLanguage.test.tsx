import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { ContentLanguageProvider } from '../language/ContentLanguage'
import { AnswerPage } from './AnswerPage'
import { renderExam } from './examTestKit'
import { FeedbackView } from './feedback/FeedbackView'
import { SummaryPage } from './SummaryPage'
import { createUploadQueue } from './uploads'
import {
  ai,
  assessment,
  attemptReport,
  metrics,
  reportResult,
  roundReport,
  setupReportsWorld,
} from './reports.mocks'
import { QUESTIONS, config, setupRoundWorld } from './rounds.mocks'
import type { AttemptReport } from './reports'

const NBSP = '\u00a0'

function englishReport(): AttemptReport {
  return attemptReport({
    question_text: 'Explain what ostracism was and why a citizen could be sent into exile.',
    model_answer: 'Ostracism was a vote on pottery shards that sent a citizen into exile for ten years.',
    result: reportResult({
      transcript: 'Ostracism was a vote on pottery shards, um, and the exile lasted ten years.',
      metrics: metrics({ filler_examples: ['um'] }),
      assessment: assessment({
        ai: ai({
          better_version: 'Ostracism was a vote, written on pottery shards, that sent a citizen into exile.',
          language_issues: [{ dimension: 'vocabulary', quote: 'a thing', suggestion: 'the exile', explanation: 'Name it.' }],
        }),
      }),
    }),
  })
}

function renderFeedback(report: AttemptReport, code: string) {
  return render(
    <MemoryRouter>
      <ContentLanguageProvider code={code}>
        <FeedbackView report={report} />
      </ContentLanguageProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  setupReportsWorld()
})

describe('rounds: content in the language of the round', () => {
  it('shows English feedback as en-GB with English quote marks and no Polish spacing', () => {
    renderFeedback(englishReport(), 'en')
    expect(document.querySelector('.content-q')).toHaveAttribute('lang', 'en-GB')
    expect(document.querySelector('.transcript')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByText(/written on pottery shards/)).toHaveAttribute('lang', 'en-GB')
    expect(document.querySelectorAll('[lang="pl-PL"]')).toHaveLength(0)
    const issue = screen.getByText(/a thing/)
    expect(issue.textContent).toContain('“a thing” → “the exile”')
    expect(screen.getByText(/“um”/)).toBeInTheDocument()
    // "a citizen" keeps an ordinary space
    expect(document.querySelector('.content-q')?.textContent).toContain('a citizen')
    expect(document.querySelector('.content-q')?.textContent).not.toContain(NBSP)
  })

  it('keeps Polish as pl-PL with Polish quotes and no-break spaces', () => {
    renderFeedback(attemptReport(), 'pl')
    expect(document.querySelector('.content-q')).toHaveAttribute('lang', 'pl-PL')
    expect(document.querySelector('.transcript')).toHaveAttribute('lang', 'pl-PL')
    expect(screen.getByText(/„ta rzecz się powtarzała” →/, { selector: 'li' }).textContent).toContain('„ta rzecz się powtarzała” → „zagrożenie tyranią powracało”')
    expect(document.querySelector('.content-q')?.textContent).toContain(`w${NBSP}Atenach`)
  })

  it("gives the exam question the round's language and the subject name none", async () => {
    setupRoundWorld({ state: { config: config({ language: 'de' }) } })
    renderExam()
    const question = await screen.findByText(QUESTIONS[0].slice(0, 20), { exact: false })
    await waitFor(() => expect(question).toHaveAttribute('lang', 'de-DE'))
    expect(screen.getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
  })

  it('treats a round without a language as Polish', async () => {
    setupRoundWorld({ state: { config: config() } })
    renderExam()
    const question = await screen.findByText(QUESTIONS[0].slice(0, 20), { exact: false })
    expect(question).toHaveAttribute('lang', 'pl-PL')
  })

  it('puts the summary rows in the round language', async () => {
    setupReportsWorld({ rounds: [roundReport({ config: config({ language: 'en' }) })] })
    render(
      <MemoryRouter initialEntries={['/rounds/5/summary']}>
        <Routes>
          <Route path="/rounds/:roundId/summary" element={<SummaryPage pollMs={20} uploads={createUploadQueue()} />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(document.querySelector('.q-line')).toHaveAttribute('lang', 'en-GB'))
    expect(screen.getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
  })

  it("puts an answer's transcript in the language of its round", async () => {
    setupReportsWorld({
      attempts: [englishReport()],
      rounds: [roundReport({ config: config({ language: 'en' }) })],
    })
    render(
      <MemoryRouter initialEntries={['/rounds/5/answers/10']}>
        <Routes>
          <Route path="/rounds/:roundId/answers/:attemptId" element={<AnswerPage uploads={createUploadQueue()} pollMs={20} />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(document.querySelector('.transcript')).toHaveAttribute('lang', 'en-GB'))
  })
})
