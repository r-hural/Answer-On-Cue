import { useState } from 'react'
import { apiSend } from '../../api/client'
import type { KeyStatus, Provider, ProvidersStatus } from '../../api/providers'
import type { KeyTestResult } from './api'
import { Notice, type Note } from '../../ui/Notice'
import { settingsStrings } from './strings'

const s = settingsStrings.keys

interface KeyNote extends Note {
  provider: Provider
}

function KeyRow({
  status,
  onStatus,
  onNote,
}: {
  status: KeyStatus
  onStatus: (status: ProvidersStatus) => void
  onNote: (note: KeyNote) => void
}) {
  const provider: Provider = status.provider
  const name = s.providers[provider]
  const titleId = `key-${provider}`
  const [value, setValue] = useState('')
  const [region, setRegion] = useState(status.region ?? '')
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<Note>) {
    setBusy(true)
    try {
      onNote({ provider, ...(await action()) })
    } catch (error) {
      onNote({ provider, text: (error as Error).message, error: true })
    } finally {
      setBusy(false)
    }
  }

  const save = () =>
    run(async () => {
      const body: Record<string, string> = { value }
      if (provider === 'azure_speech') body.region = region
      const next = await apiSend<ProvidersStatus>('PUT', `/providers/keys/${provider}`, body)
      setValue('') // the key is never kept in the page after it was saved
      onStatus(next)
      return { text: s.savedOk, error: false }
    })

  const test = () =>
    run(async () => {
      const result = await apiSend<KeyTestResult>('POST', `/providers/keys/${provider}/test`)
      return { text: result.message, error: !result.ok }
    })

  const remove = () => {
    if (!window.confirm(s.confirmRemove(name))) return
    return run(async () => {
      onStatus(await apiSend<ProvidersStatus>('DELETE', `/providers/keys/${provider}`))
      return { text: s.removed, error: false }
    })
  }

  return (
    <li aria-labelledby={titleId}>
      <div>
        <div className="title" id={titleId}>
          {name}
        </div>
        <div className="meta">{s.uses[provider]}</div>
      </div>
      <div className="key-value">
        {status.configured && status.masked ? (
          <span className="chip ok">
            <span className="dot" />
            {s.saved(status.masked)}
          </span>
        ) : (
          <span className="muted small">{s.none}</span>
        )}
        <input
          className="input"
          type="password"
          autoComplete="off"
          aria-label={`${s.input} ${name}`}
          placeholder={s.placeholder}
          // Leaves room for the region field beside it, as in the template.
          style={provider === 'azure_speech' ? { maxWidth: 160 } : undefined}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        {provider === 'azure_speech' && (
          <input
            className="input short"
            aria-label={s.region}
            placeholder={s.region}
            value={region}
            onChange={(event) => setRegion(event.target.value)}
          />
        )}
      </div>
      <div className="actions">
        <button type="button" className="btn small" onClick={save} disabled={busy || !value.trim()}>
          {s.save}
        </button>
        <button
          type="button"
          className="btn small"
          onClick={test}
          disabled={busy || !status.configured}
        >
          {s.test}
        </button>
        <button
          type="button"
          className="btn small danger"
          onClick={remove}
          disabled={busy || !status.configured}
        >
          {s.remove}
        </button>
      </div>
    </li>
  )
}

export function KeysSection({
  status,
  onStatus,
}: {
  status: ProvidersStatus
  onStatus: (status: ProvidersStatus) => void
}) {
  const [note, setNote] = useState<KeyNote | null>(null)

  return (
    <section className="set" id="keys" aria-labelledby="keys-title">
      <h2 id="keys-title">{s.title}</h2>
      <p className="lead">{s.lead}</p>
      {note && (
        <Notice kind={note.error ? 'error' : 'ok'} className="mb-4">
          <strong>{s.providers[note.provider]}</strong>
          {note.text}
        </Notice>
      )}
      <ul className="rows keys">
        {status.keys.map((key) => (
          <KeyRow key={key.provider} status={key} onStatus={onStatus} onNote={setNote} />
        ))}
      </ul>
    </section>
  )
}
