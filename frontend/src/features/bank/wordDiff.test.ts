import { describe, expect, it } from 'vitest'
import { side, wordDiff, words } from './wordDiff'

describe('wordDiff', () => {
  it('marks removed and added words', () => {
    const tokens = wordDiff('Rada liczyła pięciuset członków.', 'Rada liczyła sześciuset członków.')
    expect(tokens).toEqual([
      { text: 'Rada ', kind: 'same' },
      { text: 'liczyła ', kind: 'same' },
      { text: 'pięciuset ', kind: 'removed' },
      { text: 'sześciuset ', kind: 'added' },
      { text: 'członków.', kind: 'same' },
    ])
    expect(side(tokens, 'old').map((t) => t.text).join('')).toBe('Rada liczyła pięciuset członków.')
    expect(side(tokens, 'new').map((t) => t.text).join('')).toBe('Rada liczyła sześciuset członków.')
  })

  it('keeps whitespace and punctuation attached to the words', () => {
    expect(words('  Ateny, Sparta;\n\nTeby.')).toEqual(['  Ateny, ', 'Sparta;\n\n', 'Teby.'])
    const tokens = wordDiff('Ateny, Sparta.', 'Ateny; Sparta.')
    expect(tokens.map((t) => [t.text, t.kind])).toEqual([
      ['Ateny, ', 'removed'],
      ['Ateny; ', 'added'],
      ['Sparta.', 'same'],
    ])
  })

  it('handles Polish letters', () => {
    const tokens = wordDiff('Zażółć gęślą jaźń', 'Zażółć gęślą łódź')
    expect(tokens.filter((t) => t.kind !== 'same').map((t) => t.text)).toEqual(['jaźń', 'łódź'])
  })

  it('has no marks for an identical pair', () => {
    const text = 'Klejstenes podzielił obywateli na dziesięć fyl.'
    expect(wordDiff(text, text).every((t) => t.kind === 'same')).toBe(true)
    expect(wordDiff('', '')).toEqual([])
  })

  it('handles words added at the end and removed at the start', () => {
    const tokens = wordDiff('Wstęp Ateny', 'Ateny i Sparta')
    expect(tokens.map((t) => t.kind)).toEqual(['removed', 'same', 'added', 'added'])
  })
})
