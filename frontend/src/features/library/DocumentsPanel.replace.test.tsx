import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { extractionEstimate } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import type { Document } from './api'
import { DocumentsPanel } from './DocumentsPanel'

const document = (id: number, title: string, extracted: string | null): Document => ({
  id,
  title,
  version_count: 1,
  latest_version: {
    id: id * 10,
    version: 1,
    filename: `${title}.txt`,
    status: 'imported',
    message: null,
    char_count: 4200,
    page_count: null,
    passage_count: 3,
    topics_extracted_at: extracted,
    questions_generated_at: null,
    topic_count: 2,
  },
})

function renderPanel(documents: Document[], replaceHref?: (id: number) => string) {
  server.use(http.get('/api/costs/estimate/extraction', () => HttpResponse.json(extractionEstimate)))
  render(
    <MemoryRouter>
      <DocumentsPanel
        documents={documents}
        jobs={[]}
        selected={null}
        canRun
        uploading={false}
        uploadError={null}
        onFile={() => {}}
        onOpen={() => {}}
        onExtract={() => {}}
        chapterField={{ value: '', onChange: () => {}, names: [], defaultChapter: null }}
        onMove={() => {}}
        generateHref={() => '/generate'}
        replaceHref={replaceHref}
      />
    </MemoryRouter>,
  )
}

describe('DocumentsPanel: Replace on each row', () => {
  it('links every row to its new-version page, between Generate questions and Move', async () => {
    renderPanel(
      [document(1, 'Ateny', null), document(2, 'Sparta', '2026-10-20T10:00:00Z')],
      (id) => `/library/1/documents/${id}/new-version`,
    )
    for (const [id, title] of [
      [1, 'Ateny'],
      [2, 'Sparta'],
    ] as const) {
      const link = await screen.findByRole('link', { name: `Replace ${title} with a new version` })
      expect(link).toHaveTextContent(/^Replace$/)
      expect(link).toHaveClass('btn', 'small')
      expect(link).toHaveAttribute('href', `/library/1/documents/${id}/new-version`)
    }
    const row = screen.getByRole('link', { name: 'Replace Sparta with a new version' }).closest('li') as HTMLElement
    const names = [...row.querySelectorAll('.end > *')].map(
      (control) => control.getAttribute('aria-label') ?? control.textContent,
    )
    const generate = names.findIndex((name) => /Generate/.test(name ?? ''))
    const replace = names.findIndex((name) => /^Replace/.test(name ?? ''))
    const move = names.findIndex((name) => /Move/.test(name ?? ''))
    expect(generate).toBeGreaterThanOrEqual(0)
    expect(generate).toBeLessThan(replace)
    expect(replace).toBeLessThan(move)
  })

  it('renders no link without replaceHref', async () => {
    renderPanel([document(1, 'Ateny', null)])
    await screen.findByText('Ateny')
    expect(screen.queryByRole('link', { name: /with a new version/ })).toBeNull()
  })
})
