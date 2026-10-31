import { describe, expect, it } from 'vitest'

const sources = import.meta.glob('../**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

// A constant Polish lang in any quoting: lang="pl", lang='pl-PL', lang={'pl'}, lang={`pl-PL`}.
const POLISH_LANG = /\blang=\{?["'`]pl(-PL)?["'`]\}?/

describe('no constant Polish lang on content', () => {
  it('finds the feature sources', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50)
  })

  it('matches every spelling of a constant Polish lang', () => {
    for (const text of ['lang="pl"', "lang='pl-PL'", "lang={'pl'}", 'lang={`pl-PL`}']) {
      expect(POLISH_LANG.test(text)).toBe(true)
    }
    expect(POLISH_LANG.test('lang={bcp47}')).toBe(false)
  })

  it('has no constant Polish lang in a feature source', () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.endsWith('.test.tsx'))
      .filter(([, text]) => POLISH_LANG.test(text))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })
})
