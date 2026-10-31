import { describe, expect, it } from 'vitest'
import { LANGUAGES } from '../library/languages.mocks'
import { FALLBACK, infoFor } from './languageInfo'

describe('languageInfo', () => {
  it('has a fallback equal to the profiles', () => {
    expect(Object.keys(FALLBACK)).toHaveLength(7)
    for (const row of LANGUAGES) {
      expect(FALLBACK[row.code]).toEqual({ code: row.code, bcp47: row.bcp47, quotes: row.quotes })
    }
  })

  it('falls back to the table, then to Polish for no code', () => {
    expect(infoFor('en', null).bcp47).toBe('en-GB')
    expect(infoFor(undefined, null).code).toBe('pl')
    expect(infoFor(null, null).bcp47).toBe('pl-PL')
  })

  it('prefers a list row', () => {
    const rows = LANGUAGES.map((row) => (row.code === 'en' ? { ...row, bcp47: 'en-US' } : row))
    expect(infoFor('en', rows).bcp47).toBe('en-US')
  })

  it('gives an unknown code its own tag and English quotes', () => {
    expect(infoFor('xx', null)).toEqual({ code: 'xx', bcp47: 'xx', quotes: ['“', '”'] })
  })
})
