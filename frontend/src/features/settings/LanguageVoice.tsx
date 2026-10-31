import { useCallback, useEffect, useState } from 'react'
import {
  type DownloadState,
  type Preferences,
  type VoiceInfo,
  type VoiceProvider,
  fetchDownload,
  fetchVoices,
  previewVoice,
  startDownload,
  updateLanguage,
} from './api'
import { speakSample } from './browserVoice'
import type { ContentLanguage } from '../library/api'
import { type Note, Notice, NoteNotice } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.voice
const o = settingsStrings.openaiVoice
const l = settingsStrings.languages

export const DOWNLOAD_POLL_MS = 500
type Choice = VoiceProvider | 'default'
const CHOICES: Choice[] = ['default', 'piper', 'azure_speech', 'openai', 'browser']

function megabytes(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(1)
}

function providerLabel(provider: Choice): string {
  if (provider === 'default') return l.default
  return provider === 'openai' ? o.provider : s[provider]
}

export function LanguageVoice({
  language,
  preferences,
  keysVersion,
  onSaved,
  rate: liveRate,
}: {
  language: ContentLanguage
  preferences: Preferences
  keysVersion: number
  onSaved: (row: ContentLanguage) => void
  // The section's speech rate as it is on the slider, saved or not.
  rate?: number
}) {
  const code = language.code
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [reload, setReload] = useState(0)
  const [provider, setProvider] = useState<Choice>(
    (language.voice_provider as VoiceProvider | null) ?? 'default',
  )
  const [chosen, setChosen] = useState(language.voice_name ?? '')
  const [download, setDownload] = useState<DownloadState | null>(null)
  const [message, setMessage] = useState<Note | null>(null)

  // This language's list only: fetched on mount, after a download and when the keys change.
  useEffect(() => {
    let cancelled = false
    fetchVoices(code)
      .then((list) => !cancelled && setVoices(list))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [code, keysVersion, reload])

  const own = voices.filter((candidate) => candidate.provider === provider)
  const voice = own.some((candidate) => candidate.id === chosen) ? chosen : (own[0]?.id ?? '')
  const current = own.find((candidate) => candidate.id === voice)
  const running = download?.status === 'running' && download.voice === voice
  const rate = liveRate ?? preferences.voice_rate

  const voicesChanged = useCallback(() => setReload((n) => n + 1), [])

  useEffect(() => {
    if (!running || !download) return
    const name = download.voice
    const timer = window.setInterval(async () => {
      try {
        const next = await fetchDownload(name)
        setDownload(next)
        if (next.status === 'done') voicesChanged()
      } catch (error) {
        setDownload({
          voice: name,
          status: 'failed',
          bytes_done: 0,
          bytes_total: null,
          error: (error as Error).message,
        })
      }
    }, DOWNLOAD_POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, download, voicesChanged])

  function chooseProvider(next: Choice) {
    setProvider(next)
    setMessage(null)
  }

  async function download_() {
    try {
      setDownload(await startDownload(voice))
    } catch (error) {
      setDownload({
        voice,
        status: 'failed',
        bytes_done: 0,
        bytes_total: null,
        error: (error as Error).message,
      })
    }
  }

  async function refresh() {
    try {
      setVoices(await fetchVoices(code, { refresh: true }))
      setMessage({ text: l.refreshed, error: false })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  async function preview() {
    if (provider === 'default') return
    try {
      if (provider === 'browser') {
        if (!speakSample(code, language.bcp47, rate)) {
          setMessage({ text: l.noBrowserVoice(language.name_en), error: true })
        } else setMessage(null)
        return
      }
      const result = await previewVoice(provider, voice, rate, code)
      if (!result.audio_url) throw new Error(s.previewFailed)
      await new Audio(result.audio_url).play()
      setMessage(null)
    } catch (error) {
      setMessage({ text: (error as Error).message || s.previewFailed, error: true })
    }
  }

  async function save() {
    try {
      const patch =
        provider === 'default'
          ? { voice_provider: null }
          : provider === 'browser'
            ? { voice_provider: provider, voice_name: null }
            : { voice_provider: provider, voice_name: voice }
      onSaved(await updateLanguage(code, patch))
      setMessage({ text: s.saved, error: false })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    }
  }

  const needsDownload = provider === 'piper' && current && !current.installed
  const progress = download && download.voice === voice ? download : null
  const total = progress?.bytes_total ?? null
  const azureMissing = provider === 'azure_speech' && (own.length === 0 || !current?.installed)

  let defaultLine: string = l.defaultOther
  if (code === 'pl') {
    const globalProvider = preferences.voice_provider
    const globalVoice =
      voices.find((v) => v.provider === globalProvider && v.id === preferences.voice_name)?.label ??
      preferences.voice_name
    defaultLine = l.defaultPolish(providerLabel(globalProvider), globalVoice)
  }

  return (
    <div className="mt-4">
      <fieldset>
        <legend>{s.provider}</legend>
        <div className="choice-group">
          {CHOICES.map((name) => (
            <label key={name} className="choice">
              <input
                type="radio"
                name={`voice-provider-${code}`}
                checked={provider === name}
                onChange={() => chooseProvider(name)}
              />
              {providerLabel(name)}
            </label>
          ))}
        </div>
      </fieldset>
      {provider === 'default' && <p className="hint">{defaultLine}</p>}
      {provider !== 'default' && provider !== 'browser' && (
        <div className="fields mt-5">
          <div className="field">
            <label className="label" htmlFor={`voice-${code}`}>
              {s.voice}
            </label>
            <select
              className="select"
              id={`voice-${code}`}
              value={voice}
              onChange={(event) => setChosen(event.target.value)}
            >
              {own.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.label}
                  {candidate.installed ? '' : provider === 'openai' ? o.noKey : s.notInstalled}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      {azureMissing && (
        <Notice kind="warn" className="mt-5">
          {s.azureMissing}
        </Notice>
      )}
      {provider === 'openai' && current && !current.installed && (
        <Notice kind="warn" className="mt-5">
          {o.keyMissing}
        </Notice>
      )}
      {provider === 'openai' && <p className="hint">{o.hint}</p>}
      {provider !== 'default' && (
        <div className="actions">
          <button
            type="button"
            className="btn"
            onClick={preview}
            disabled={provider !== 'browser' && !current?.installed}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 8v4h3l4 3V5L6 8H3z" />
              <path d="M13.5 7.5a3.5 3.5 0 0 1 0 5" />
            </svg>
            {s.preview}
          </button>
        </div>
      )}
      <div className="actions mt-3">
        {needsDownload && (
          <button type="button" className="btn" onClick={download_} disabled={running}>
            {s.download}
          </button>
        )}
        {needsDownload && progress?.status === 'running' && (
          <div className="download" role="status">
            <p className="small">
              <span className="num">
                {total
                  ? s.downloading(megabytes(progress.bytes_done), megabytes(total))
                  : s.downloadingUnknown(megabytes(progress.bytes_done))}
              </span>
            </p>
            {total ? (
              <div
                className="meter-bar"
                role="progressbar"
                aria-label={s.downloadProgress}
                aria-valuemin={0}
                aria-valuemax={total}
                aria-valuenow={progress.bytes_done}
              >
                <span
                  style={{
                    width: `${Math.min(100, (progress.bytes_done / total) * 100)}%`,
                  }}
                />
              </div>
            ) : (
              <div
                className="meter-bar indeterminate"
                role="progressbar"
                aria-label={s.downloadProgress}
              >
                <span />
              </div>
            )}
            <p className="hint">{s.downloadHint}</p>
          </div>
        )}
        <button
          type="button"
          className="btn"
          onClick={save}
          disabled={provider === 'azure_speech' && own.length === 0}
        >
          {s.save}
        </button>
        <button type="button" className="btn" onClick={refresh}>
          {l.refresh}
        </button>
      </div>
      {needsDownload && progress?.status === 'failed' && (
        <p className="small text-error mt-2" role="alert">
          {progress.error || s.downloadFailed}
        </p>
      )}
      <NoteNotice note={message} />
    </div>
  )
}
