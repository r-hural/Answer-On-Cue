import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { subjectHandler } from './language.mocks'
import { useSubjectLanguage } from './useSubjectLanguage'

describe('useSubjectLanguage', () => {
  it('is null until the subject loads, then its code', async () => {
    server.use(subjectHandler({ id: 5, language: 'de' }))
    const { result } = renderHook(() => useSubjectLanguage(5))
    expect(result.current).toBeNull()
    await waitFor(() => expect(result.current).toBe('de'))
  })

  it('is Polish for a subject without a language', async () => {
    server.use(subjectHandler({ id: 6 }))
    const { result } = renderHook(() => useSubjectLanguage(6))
    await waitFor(() => expect(result.current).toBe('pl'))
  })

  it('makes no request without an id', async () => {
    let calls = 0
    server.use(
      http.get('/api/subjects/:id', () => {
        calls += 1
        return HttpResponse.json({})
      }),
    )
    const { result } = renderHook(() => useSubjectLanguage(undefined))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current).toBeNull()
    expect(calls).toBe(0)
  })
})
