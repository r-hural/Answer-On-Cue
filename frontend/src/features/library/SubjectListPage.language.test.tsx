import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { languagesWith } from './languages.mocks'
import { SubjectListPage } from './SubjectListPage'

const NOT_ENABLED = 'This content language is not enabled. Choose one of the enabled languages.'

function renderPage() {
  return render(
    <MemoryRouter>
      <SubjectListPage />
    </MemoryRouter>,
  )
}

describe('SubjectListPage content language', () => {
  it('requires a language and sends it', async () => {
    let created: unknown
    server.use(
      http.get('/api/subjects', () => HttpResponse.json([])),
      http.post('/api/subjects', async ({ request }) => {
        created = await request.json()
        return HttpResponse.json(
          { id: 2, name: 'Biologia', description: null, document_count: 0, language: 'pl' },
          { status: 201 },
        )
      }),
    )
    const { container } = renderPage()
    const select = await screen.findByRole('combobox', { name: 'Content language' })
    await screen.findByRole('option', { name: 'Polish (polski)' })

    const description = screen.getByLabelText('Description (optional)')
    const button = screen.getByRole('button', { name: 'Add subject' })
    expect(description.compareDocumentPosition(select) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(select.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    await userEvent.type(screen.getByLabelText('Subject name'), 'Biologia')
    expect(button).toBeDisabled()
    await userEvent.selectOptions(select, 'Polish (polski)')
    expect(button).toBeEnabled()
    await userEvent.click(button)
    expect(await screen.findByRole('link', { name: 'Biologia' })).toBeInTheDocument()
    expect(created).toEqual({ name: 'Biologia', description: null, language: 'pl' })
    expect(select).toHaveValue('')
    expect(screen.getByRole('option', { name: 'Choose a language' })).toHaveProperty('selected', true)
    expect(container.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('lists every enabled language and shows the refusal', async () => {
    server.use(
      http.get('/api/subjects', () => HttpResponse.json([])),
      http.get('/api/languages', () => HttpResponse.json(languagesWith('de'))),
      http.post('/api/subjects', () => HttpResponse.json({ detail: NOT_ENABLED }, { status: 422 })),
    )
    renderPage()
    expect(await screen.findByRole('option', { name: 'German (Deutsch)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Polish (polski)' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Subject name'), 'Geschichte')
    await userEvent.selectOptions(screen.getByLabelText('Content language'), 'German (Deutsch)')
    await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(NOT_ENABLED)
  })

  it('says when the languages cannot be loaded', async () => {
    server.use(
      http.get('/api/subjects', () => HttpResponse.json([])),
      http.get('/api/languages', () => HttpResponse.json({ detail: 'Database locked.' }, { status: 500 })),
    )
    renderPage()
    expect(await screen.findByText('Could not load the languages.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Subject name'), 'Biologia')
    expect(screen.getByRole('button', { name: 'Add subject' })).toBeDisabled()
  })
})
