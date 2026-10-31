import { plNbsp, typeset } from './polish'

describe('plNbsp', () => {
  it('binds one-letter words to the next word', () => {
    expect(plNbsp('Wyjaśnij, na czym polegała procedura w Atenach i jaki był cel.')).toBe(
      'Wyjaśnij, na czym polegała procedura w Atenach i jaki był cel.',
    )
  })

  it('handles capitals, the start of the text and chains of short words', () => {
    expect(plNbsp('A potem z i w domu')).toBe('A potem z i w domu')
  })

  it('leaves longer words and letters inside words alone', () => {
    expect(plNbsp('Demokracja ateńska ma sens')).toBe('Demokracja ateńska ma sens')
  })

  it('works after an opening quote', () => {
    expect(plNbsp('„a więc tak')).toBe('„a więc tak')
  })
})

describe('typeset', () => {
  it('applies the one-letter rule to Polish only', () => {
    expect(typeset('Iść w las', 'pl')).toBe(plNbsp('Iść w las'))
    expect(typeset('Iść w las', 'pl')).toContain('w\u00a0las')
    expect(typeset('a cat', 'en')).toBe('a cat')
    expect(typeset('у місті', 'uk')).toBe('у місті')
  })

  it('treats a missing code as Polish', () => {
    expect(typeset('w lesie', null)).toBe('w\u00a0lesie')
    expect(typeset('w lesie', undefined)).toBe('w\u00a0lesie')
  })

  it('keeps plNbsp as it was', () => {
    expect(plNbsp('w lesie')).toBe('w\u00a0lesie')
  })
})
