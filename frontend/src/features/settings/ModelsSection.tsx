import { useState } from 'react'
import { type Preferences, type TextProvider, savePreferences } from './api'
import { type Note, NoteNotice } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.models

type ModelFields = Pick<
  Preferences,
  | 'transcription_model'
  | 'generation_provider'
  | 'generation_model'
  | 'assessment_provider'
  | 'assessment_model'
>

/** A field label whose visible text is short; the role is added for screen readers. */
function RoleLabel({ htmlFor, text, role }: { htmlFor: string; text: string; role: string }) {
  return (
    <label className="label" htmlFor={htmlFor}>
      {text}
      <span className="visually-hidden"> ({role})</span>
    </label>
  )
}

export function ModelsSection({
  preferences,
  onSaved,
}: {
  preferences: Preferences
  onSaved: (preferences: Preferences) => void
}) {
  const [form, setForm] = useState<ModelFields>({
    transcription_model: preferences.transcription_model,
    generation_provider: preferences.generation_provider,
    generation_model: preferences.generation_model,
    assessment_provider: preferences.assessment_provider,
    assessment_model: preferences.assessment_model,
  })
  const [message, setMessage] = useState<Note | null>(null)
  const set = <K extends keyof ModelFields>(key: K, value: ModelFields[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  async function save() {
    try {
      onSaved(await savePreferences(form))
      setMessage({ text: s.saved, error: false })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  const suggestions = Array.from(
    new Set([
      preferences.transcription_model,
      preferences.generation_model,
      preferences.assessment_model,
    ]),
  )

  function modelField(title: string, key: keyof ModelFields & `${string}_model`) {
    const id = `m-${key}`
    return (
      <div className="field">
        <RoleLabel htmlFor={id} text={s.model} role={title} />
        <input
          className="input"
          id={id}
          list="model-suggestions"
          value={form[key]}
          onChange={(event) => set(key, event.target.value)}
        />
      </div>
    )
  }

  function role(
    title: string,
    providerKey: 'generation_provider' | 'assessment_provider',
    modelKey: 'generation_model' | 'assessment_model',
  ) {
    const id = `m-${providerKey}`
    return (
      <fieldset className="mt-6">
        <legend>
          <h3>{title}</h3>
        </legend>
        <div className="fields wide">
          <div className="field">
            <RoleLabel htmlFor={id} text={s.provider} role={title} />
            <select
              className="select"
              id={id}
              value={form[providerKey]}
              onChange={(event) => set(providerKey, event.target.value as TextProvider)}
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          {modelField(title, modelKey)}
        </div>
      </fieldset>
    )
  }

  return (
    <section className="set" id="models" aria-labelledby="models-title">
      <h2 id="models-title">{s.title}</h2>
      <datalist id="model-suggestions">
        {suggestions.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
      <fieldset className="mt-6">
        <legend>
          <h3>{s.transcription}</h3>
        </legend>
        {modelField(s.transcription, 'transcription_model')}
      </fieldset>
      {role(s.generation, 'generation_provider', 'generation_model')}
      {role(s.assessment, 'assessment_provider', 'assessment_model')}
      <div className="actions">
        <button type="button" className="btn" onClick={save}>
          {s.save}
        </button>
      </div>
      <NoteNotice note={message} />
    </section>
  )
}
