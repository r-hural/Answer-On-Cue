import { useEffect, useState } from 'react'
import { type ProvidersStatus, fetchProvidersStatus } from '../../api/providers'
import { PrivacyNotice } from '../../i18n/Privacy'
import { type Preferences, fetchPreferences } from './api'
import { KeysSection } from './KeysSection'
import { LanguagesSection } from './LanguagesSection'
import { ModelsSection } from './ModelsSection'
import { ScoringSection } from './ScoringSection'
import { SpendingSection } from './SpendingSection'
import { StorageSection } from './StorageSection'
import { Notice } from '../../ui/Notice'
import { settingsStrings } from './strings'
import { TimingSection } from './TimingSection'

interface Loaded {
  status: ProvidersStatus
  preferences: Preferences
}

const SECTIONS = [
  { id: 'keys', label: settingsStrings.keys.title },
  { id: 'models', label: settingsStrings.models.title },
  { id: 'languages', label: settingsStrings.languages.title },
  { id: 'scoring', label: settingsStrings.scoring.title },
  { id: 'targets', label: settingsStrings.timing.title },
  { id: 'storage', label: settingsStrings.storage.title },
  { id: 'spending', label: settingsStrings.spending.title },
  { id: 'privacy', label: settingsStrings.privacy },
]

export function SettingsPage() {
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [current, setCurrent] = useState(SECTIONS[0].id)
  const [keysVersion, setKeysVersion] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchProvidersStatus(), fetchPreferences()])
      .then(
        ([status, preferences]) => !cancelled && setData({ status, preferences }),
      )
      .catch(() => !cancelled && setError(settingsStrings.loadError))
    return () => {
      cancelled = true
    }
  }, [])

  const head = (
    <div className="page-head">
      <h1>{settingsStrings.title}</h1>
    </div>
  )

  if (error)
    return (
      <>
        {head}
        <Notice kind="error">{error}</Notice>
      </>
    )
  if (!data)
    return (
      <>
        {head}
        <p className="muted">{settingsStrings.loading}</p>
      </>
    )

  // Keys change which voices are usable (Azure, OpenAI), so each language reloads its list.
  const onStatus = (status: ProvidersStatus) => {
    setData((current) => (current ? { ...current, status } : current))
    setKeysVersion((version) => version + 1)
  }
  const onSaved = (preferences: Preferences) =>
    setData((current) => (current ? { ...current, preferences } : current))

  return (
    <>
      {head}
      <div className="split">
        <nav className="side-nav" aria-label={settingsStrings.sections}>
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              aria-current={current === section.id ? 'true' : undefined}
              onClick={() => setCurrent(section.id)}
            >
              {section.label}
            </a>
          ))}
        </nav>
        <div>
          <KeysSection status={data.status} onStatus={onStatus} />
          <ModelsSection preferences={data.preferences} onSaved={onSaved} />
          <LanguagesSection
            preferences={data.preferences}
            keysVersion={keysVersion}
            onPreferences={onSaved}
          />
          <ScoringSection preferences={data.preferences} onSaved={onSaved} />
          <TimingSection preferences={data.preferences} onSaved={onSaved} />
          <StorageSection />
          <SpendingSection preferences={data.preferences} onSaved={onSaved} />
          <section className="set" id="privacy" aria-labelledby="privacy-title">
            <h2 id="privacy-title">{settingsStrings.privacy}</h2>
            <div className="mt-4">
              <PrivacyNotice />
            </div>
          </section>
        </div>
      </div>
    </>
  )
}
