import type { ContentLanguage } from './api'
import { libraryStrings } from './strings'

const s = libraryStrings.languages

/** The content language of a new subject: enabled languages only, nothing preselected. A
 * setting, not content: sans, no lang attribute. */
export function LanguageField(props: {
  id: string
  languages: ContentLanguage[]
  value: string
  onChange: (code: string) => void
  disabled?: boolean
}) {
  const { id, languages, value, onChange, disabled } = props
  const hint = `${id}-hint`
  return (
    <div className="field">
      <label className="label" htmlFor={id}>
        {s.label}
      </label>
      <select
        className="select"
        id={id}
        value={value}
        disabled={disabled}
        aria-describedby={hint}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="" disabled>
          {s.choose}
        </option>
        {languages
          .filter((language) => language.enabled)
          .map((language) => (
            <option key={language.code} value={language.code}>
              {s.option(language.name_en, language.name_native)}
            </option>
          ))}
      </select>
      <span className="hint" id={hint}>
        {s.hint}
      </span>
    </div>
  )
}
