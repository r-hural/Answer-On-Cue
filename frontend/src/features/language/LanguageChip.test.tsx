import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LANGUAGES, languagesWith } from '../library/languages.mocks'
import { LanguageChip } from './LanguageChip'

describe('LanguageChip', () => {
  it('shows nothing while one language is enabled, or before the list loads', () => {
    const { container, rerender } = render(<LanguageChip code="pl" languages={LANGUAGES} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LanguageChip code="pl" languages={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the language of the subject', () => {
    render(<LanguageChip code="en" languages={languagesWith('en')} />)
    const chip = screen.getByText('English')
    expect(chip).toHaveAttribute('title', 'English')
    expect(chip).not.toHaveAttribute('lang')
  })

  it('shows the native name as the title, and Polish for no code', () => {
    const { rerender } = render(<LanguageChip code="pl" languages={languagesWith('en')} />)
    expect(screen.getByText('Polish')).toHaveAttribute('title', 'polski')
    rerender(<LanguageChip code={undefined} languages={languagesWith('en')} />)
    expect(screen.getByText('Polish')).toBeInTheDocument()
  })
})
