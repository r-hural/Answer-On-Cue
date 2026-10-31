import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LanguageField } from './LanguageField'
import { languagesWith } from './languages.mocks'

describe('LanguageField', () => {
  it('lists the placeholder and the enabled languages, nothing preselected', async () => {
    const onChange = vi.fn()
    render(<LanguageField id="lang" languages={languagesWith('de')} value="" onChange={onChange} />)
    const select = screen.getByRole('combobox', { name: 'Content language' })
    const options = within(select).getAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'Choose a language',
      'Polish (polski)',
      'German (Deutsch)',
    ])
    expect(select).toHaveValue('')
    expect(options[0]).toBeDisabled()
    expect((options[0] as HTMLOptionElement).selected).toBe(true)
    expect(within(select).queryByRole('option', { name: /French/ })).toBeNull()

    const hint = screen.getByText("The language of the materials, questions and answers. It can't be changed later.")
    expect(select).toHaveAttribute('aria-describedby', hint.id)
    expect(select).not.toHaveAttribute('lang')
    expect(select).not.toHaveClass('content')

    await userEvent.selectOptions(select, 'de')
    expect(onChange).toHaveBeenCalledWith('de')
  })

  it('shows one name when the English and native names are equal', () => {
    render(<LanguageField id="lang" languages={languagesWith('en')} value="" onChange={() => undefined} />)
    expect(screen.getByRole('option', { name: 'English' })).toBeInTheDocument()
  })
})
