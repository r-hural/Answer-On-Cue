import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { LANGUAGES } from '../library/languages.mocks'
import { ContentLanguageProvider, useContentLanguage } from './ContentLanguage'

function Probe() {
  const value = useContentLanguage()
  return (
    <p>
      <span data-testid="tag">{value.bcp47}</span>
      <span data-testid="quote">{value.quote('x')}</span>
      <span data-testid="typeset">{value.typeset('a b w c')}</span>
    </p>
  )
}

describe('useContentLanguage', () => {
  it('is Polish without a provider', () => {
    render(<Probe />)
    expect(screen.getByTestId('tag')).toHaveTextContent('pl-PL')
    expect(screen.getByTestId('quote').textContent).toBe('„x”')
    expect(screen.getByTestId('typeset').textContent).toBe('a\u00a0b w\u00a0c')
  })

  it('follows the code of the provider', () => {
    render(
      <ContentLanguageProvider code="fr">
        <Probe />
      </ContentLanguageProvider>,
    )
    expect(screen.getByTestId('tag')).toHaveTextContent('fr-FR')
    const fr = LANGUAGES.find((row) => row.code === 'fr')!
    expect(screen.getByTestId('quote').textContent).toBe(fr.quotes[0] + 'x' + fr.quotes[1])
    expect(screen.getByTestId('quote').textContent).toBe('« x »')
    expect(screen.getByTestId('typeset').textContent).toBe('a b w c')
  })

  it('is Polish for a missing code', () => {
    render(
      <ContentLanguageProvider code={null}>
        <Probe />
      </ContentLanguageProvider>,
    )
    expect(screen.getByTestId('tag')).toHaveTextContent('pl-PL')
  })

  it('fetches the list once', async () => {
    let calls = 0
    server.use(
      http.get('/api/languages', () => {
        calls += 1
        return HttpResponse.json(LANGUAGES)
      }),
    )
    const { rerender } = render(
      <ContentLanguageProvider code="en">
        <Probe />
      </ContentLanguageProvider>,
    )
    await waitFor(() => expect(calls).toBe(1))
    rerender(
      <ContentLanguageProvider code="de">
        <Probe />
      </ContentLanguageProvider>,
    )
    expect(screen.getByTestId('tag')).toHaveTextContent('de-DE')
    expect(calls).toBe(1)
  })

  it('keeps the fallback when the list fails', async () => {
    let calls = 0
    server.use(
      http.get('/api/languages', () => {
        calls += 1
        return new HttpResponse(null, { status: 500 })
      }),
    )
    render(
      <ContentLanguageProvider code="en">
        <Probe />
      </ContentLanguageProvider>,
    )
    await waitFor(() => expect(calls).toBe(1))
    expect(screen.getByTestId('tag')).toHaveTextContent('en-GB')
  })
})
