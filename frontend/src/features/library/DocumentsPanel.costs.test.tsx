import { render, screen, waitFor } from '@testing-library/react'
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
  },
})

function renderPanel(documents: Document[]) {
  const asked: string[] = []
  server.use(
    http.get('/api/costs/estimate/extraction', ({ request }) => {
      asked.push(new URL(request.url).search)
      return HttpResponse.json(extractionEstimate)
    }),
  )
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
        generateHref={() => '/'}
      />
    </MemoryRouter>,
  )
  return asked
}

describe('DocumentsPanel: the estimate before topic extraction', () => {
  it('shows the line in the meta of a document awaiting analysis', async () => {
    const asked = renderPanel([document(1, 'Ateny', null)])
    const line = await screen.findByText('Analysing topics: about $0.04 of AI usage.')
    expect(line.closest('p.meta')).not.toBeNull()
    expect(asked).toEqual(['?document_version_id=10'])
  })

  it('shows nothing and asks nothing for a document with topics', async () => {
    const asked = renderPanel([document(2, 'Sparta', '2026-10-20T10:00:00Z')])
    await screen.findByText('Sparta')
    await waitFor(() => expect(screen.queryByText(/Analysing topics/)).toBeNull())
    expect(asked).toEqual([])
  })
})
