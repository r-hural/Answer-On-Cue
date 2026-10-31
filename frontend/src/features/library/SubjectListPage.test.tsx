import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { SubjectListPage } from './SubjectListPage'

describe('SubjectListPage', () => {
  it('lists subjects and creates a new one', async () => {
    let created: unknown
    server.use(
      http.get('/api/subjects', () =>
        HttpResponse.json([{ id: 1, name: 'Historia', description: null, document_count: 2 }]),
      ),
      http.post('/api/subjects', async ({ request }) => {
        created = await request.json()
        return HttpResponse.json(
          { id: 2, name: 'Biologia', description: 'Komórka', document_count: 0 },
          { status: 201 },
        )
      }),
    )
    render(
      <MemoryRouter>
        <SubjectListPage />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('link', { name: 'Historia' })).toHaveAttribute('href', '/library/1')
    expect(screen.getByText(/Documents: 2/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Subject name'), 'Biologia')
    await userEvent.type(screen.getByLabelText('Description (optional)'), 'Komórka')
    await userEvent.selectOptions(screen.getByLabelText('Content language'), 'pl')
    await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
    expect(await screen.findByRole('link', { name: 'Biologia' })).toBeInTheDocument()
    expect(created).toEqual({ name: 'Biologia', description: 'Komórka', language: 'pl' })
    expect(screen.getByLabelText('Subject name')).toHaveValue('')
  })

  it('shows the backend message for a duplicate name', async () => {
    server.use(
      http.get('/api/subjects', () => HttpResponse.json([])),
      http.post('/api/subjects', () =>
        HttpResponse.json({ detail: 'A subject with this name already exists.' }, { status: 409 }),
      ),
    )
    render(
      <MemoryRouter>
        <SubjectListPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('No subjects yet. Add the first one.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Subject name'), 'Historia')
    await userEvent.selectOptions(screen.getByLabelText('Content language'), 'pl')
    await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A subject with this name already exists.')
  })
})
