import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { BankReviewPage } from '../bank/BankReviewPage'
import { ContentLanguageProvider } from '../language/ContentLanguage'
import { subjectHandler } from '../language/language.mocks'
import { LANGUAGES, languagesWith, setupWorld, topic } from './library.mocks'
import { RemoveDialog } from './RemoveDialog'
import { SubjectListPage } from './SubjectListPage'
import { SubjectPage } from './SubjectPage'
import { TopicsEditPage } from './TopicsEditPage'

const NBSP = ' '

function languagesAre(rows: typeof LANGUAGES) {
  server.use(http.get('/api/languages', () => HttpResponse.json(rows)))
}

function renderSubject() {
  return render(
    <MemoryRouter initialEntries={['/library/1']}>
      <Routes>
        <Route path="/library/:subjectId" element={<SubjectPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SubjectPage: content in the subject language', () => {
  it('shows an English subject as en-GB, with the name and description plain, and the chip', async () => {
    setupWorld({ topics: [topic({ title: 'Athenian democracy', description: 'How it worked', objectives: [] })] })
    server.use(subjectHandler({ id: 1, name: 'History', description: 'A course', language: 'en' }))
    languagesAre(languagesWith('en'))
    renderSubject()
    const title = await screen.findByText('Athenian democracy')
    await waitFor(() => expect(title).toHaveAttribute('lang', 'en-GB'))
    expect(screen.getByText('How it worked')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByText('Eklezja')).toHaveAttribute('lang', 'en-GB')
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent('History')
    expect(heading).not.toHaveAttribute('lang')
    expect(screen.getByText('A course')).not.toHaveAttribute('lang')
    const chip = await screen.findByText('English')
    expect(chip).toHaveClass('chip')
    expect(heading.parentElement).toContainElement(chip)
  })

  it('shows no chip while one language is enabled', async () => {
    setupWorld({ topics: [topic()] })
    renderSubject()
    await screen.findByText('Ustrój')
    await waitFor(() => expect(screen.getByText('Ustrój')).toHaveAttribute('lang', 'pl-PL'))
    expect(document.querySelector('.page-head .chip')).toBeNull()
  })
})

describe('SubjectListPage: the language chip', () => {
  it('puts a chip first in each row once two languages are enabled, and no lang on the names', async () => {
    server.use(
      http.get('/api/subjects', () =>
        HttpResponse.json([
          { id: 1, name: 'Historia', description: 'Opis', document_count: 2, language: 'pl' },
          { id: 2, name: 'History', description: null, document_count: 1, language: 'en' },
        ]),
      ),
    )
    languagesAre(languagesWith('en'))
    render(
      <MemoryRouter>
        <SubjectListPage />
      </MemoryRouter>,
    )
    const polish = await screen.findByRole('link', { name: 'Historia' })
    const english = screen.getByRole('link', { name: 'History' })
    expect(polish).not.toHaveAttribute('lang')
    expect(english).not.toHaveAttribute('lang')
    expect(screen.getByText('Opis')).not.toHaveAttribute('lang')
    await waitFor(() => expect(document.querySelectorAll('li .meta .chip')).toHaveLength(2))
    const metas = [...document.querySelectorAll('li p.meta')]
    expect(metas.map((meta) => meta.firstElementChild?.textContent)).toEqual(['Polish', 'English'])
  })

  it('shows no chip while one language is enabled', async () => {
    server.use(
      http.get('/api/subjects', () =>
        HttpResponse.json([{ id: 1, name: 'Historia', description: null, document_count: 2, language: 'pl' }]),
      ),
    )
    render(
      <MemoryRouter>
        <SubjectListPage />
      </MemoryRouter>,
    )
    await screen.findByRole('link', { name: 'Historia' })
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(screen.queryByText('Polish', { selector: '.chip' })).toBeNull()
  })
})

describe('TopicsEditPage: content in the subject language', () => {
  it('sets the topic name to de-DE and leaves the subject link plain', async () => {
    setupWorld({ topics: [topic({ title: 'Verfassung' })] })
    server.use(subjectHandler({ id: 1, name: 'Geschichte', language: 'de' }))
    render(
      <MemoryRouter initialEntries={['/library/1/topics']}>
        <Routes>
          <Route path="/library/:subjectId/topics" element={<TopicsEditPage />} />
        </Routes>
      </MemoryRouter>,
    )
    const name = await screen.findByDisplayValue('Verfassung')
    await waitFor(() => expect(name).toHaveAttribute('lang', 'de-DE'))
    expect(screen.getByRole('link', { name: 'Geschichte' })).not.toHaveAttribute('lang')
  })
})

describe('RemoveDialog: quote marks of the language', () => {
  it('uses German quotes under de', () => {
    render(
      <ContentLanguageProvider code="de">
        <RemoveDialog topic={topic({ title: 'Titel' })} busy={false} onCancel={() => undefined} onConfirm={() => undefined} />
      </ContentLanguageProvider>,
    )
    expect(screen.getByRole('heading', { level: 2 }).textContent).toContain('„Titel“')
  })

  it('uses Polish quotes without a provider', () => {
    render(<RemoveDialog topic={topic({ title: 'Tytuł' })} busy={false} onCancel={() => undefined} onConfirm={() => undefined} />)
    expect(screen.getByRole('heading', { level: 2 }).textContent).toContain('„Tytuł”')
  })
})

function question(id: number, text: string, language: string) {
  return {
    version_id: id,
    family_id: id,
    version: 1,
    text,
    style: 'open',
    difficulty: 'recall',
    primary_topic_id: 5,
    status: 'active',
    review_note: null,
    rubric: {
      essential_points: [{ id: 'p1', text: `Key point ${language}`, weight: 1, alternatives: [], passage_ids: [100] }],
      enrichment_points: [],
      misconceptions: [],
      major_error_rules: [],
      model_answer: `Model answer ${language}`,
      expanded_explanation: null,
    },
    passages: [{ id: 100, locator: 'section 1', heading: null, text: 'The assembly met on the Pnyx.' }],
  }
}

function renderBank(language: string, text: string, name = 'History') {
  server.use(
    http.get('/api/subjects/1', () => HttpResponse.json({ id: 1, name, language })),
    http.get('/api/topics', () => HttpResponse.json([{ id: 5, title: 'Athens' }])),
    http.get('/api/questions', () => HttpResponse.json([{ version_id: 11 }])),
    http.get('/api/questions/reviews', () => HttpResponse.json([])),
    http.get('/api/questions/:id', () => HttpResponse.json(question(11, text, language))),
  )
  return render(
    <MemoryRouter initialEntries={['/library/1/questions']}>
      <Routes>
        <Route path="/library/:subjectId/questions" element={<BankReviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BankReviewPage: content in the subject language', () => {
  it('shows an English question, key points and model answer as en-GB, and the subject link plain', async () => {
    renderBank('en', 'Explain what a citizen could be sent into exile for.')
    const heading = await screen.findByRole('heading', { level: 3, name: /Explain what/ })
    await waitFor(() => expect(heading).toHaveAttribute('lang', 'en-GB'))
    expect(heading.textContent).not.toContain(NBSP)
    expect(screen.getByText('Key point en').closest('[lang]')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByText('Model answer en', { exact: false })).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByRole('link', { name: 'History' })).not.toHaveAttribute('lang')
  })

  it('keeps a Polish question as pl-PL with no-break spaces', async () => {
    renderBank('pl', 'Co to w Atenach było?', 'Historia')
    const heading = await screen.findByRole('heading', { level: 3, name: /Co to/ })
    await waitFor(() => expect(heading).toHaveAttribute('lang', 'pl-PL'))
    expect(heading.textContent).toContain(`w${NBSP}Atenach`)
    expect(within(document.body).getByRole('link', { name: 'Historia' })).not.toHaveAttribute('lang')
  })
})
