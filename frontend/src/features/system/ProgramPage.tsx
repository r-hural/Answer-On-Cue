import { useCallback, useEffect, useRef, useState } from 'react'
import { getHealthVersion, getInfo, postUpdate, type SystemInfo } from './api'
import { systemStrings as t } from './strings'

export const POLL_INTERVAL_MS = 2000
export const UPDATE_TIMEOUT_MS = 10 * 60 * 1000

type UpdateState = 'idle' | 'running' | 'timeout'

export function ProgramPage({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updateState, setUpdateState] = useState<UpdateState>('idle')
  const startVersion = useRef<string | null>(null)

  const recheck = useCallback(() => {
    setError(null)
    getInfo(true)
      .then(setInfo)
      .catch(() => setError(t.loadError))
  }, [])

  useEffect(() => {
    let cancelled = false
    getInfo()
      .then((data) => !cancelled && setInfo(data))
      .catch(() => !cancelled && setError(t.loadError))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (updateState !== 'running') return
    let stopped = false
    const poll = setInterval(async () => {
      const version = await getHealthVersion()
      if (!stopped && version && version !== startVersion.current) {
        stopped = true
        clearInterval(poll)
        reload()
      }
    }, POLL_INTERVAL_MS)
    const giveUp = setTimeout(() => {
      stopped = true
      clearInterval(poll)
      setUpdateState('timeout')
    }, UPDATE_TIMEOUT_MS)
    return () => {
      stopped = true
      clearInterval(poll)
      clearTimeout(giveUp)
    }
  }, [updateState, reload])

  async function handleUpdate() {
    if (!info || !window.confirm(t.confirmUpdate)) return
    setError(null)
    const failure = await postUpdate().catch(() => '')
    if (failure !== null) {
      setError(failure || t.updateFailed)
      return
    }
    startVersion.current = info.version
    setUpdateState('running')
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t.heading}</h1>
          <p>{t.lede}</p>
        </div>
      </div>
      {error && (
        <div className="notice error mb-6" role="alert">
          {ERROR_ICON}
          <div>{error}</div>
        </div>
      )}
      {!info && !error && <p className="muted">{t.loading}</p>}
      {info && updateState === 'running' && (
        <section className="panel flow" role="status">
          {info.latest && <h2>{t.updatingTo(info.latest)}</h2>}
          <div className="meter-bar indeterminate" role="progressbar" aria-label={t.updateProgress}>
            <span />
          </div>
          <p className="lead">{t.updating}</p>
        </section>
      )}
      {info && updateState !== 'running' && (
        <section className="box flow" aria-labelledby="version-h">
          <h2 id="version-h">{t.version(info.version)}</h2>
          {info.update_available && info.latest ? (
            <div className="notice">
              {UPDATE_ICON}
              <div>
                <strong>{t.updateAvailable(info.latest)}</strong>
              </div>
            </div>
          ) : info.latest ? (
            <div className="notice ok">
              {OK_ICON}
              <div>
                <strong>{t.upToDate}</strong>
              </div>
            </div>
          ) : (
            info.check_message && (
              <div className="notice">
                {INFO_ICON}
                <div>{info.check_message}</div>
              </div>
            )
          )}
          {info.last_update && !info.last_update.ok && (
            <div className="notice error" role="alert">
              {ERROR_ICON}
              <div>{t.lastUpdateFailed(info.last_update.message ?? '')}</div>
            </div>
          )}
          {updateState === 'timeout' && (
            <div className="notice warn" role="alert">
              {WARN_ICON}
              <div>{t.updateTooLong}</div>
            </div>
          )}
          <div className="actions">
            {info.update_available && info.installed && updateState === 'idle' && (
              <button type="button" className="btn primary" onClick={handleUpdate}>
                {t.update}
              </button>
            )}
            <button type="button" className="btn" onClick={recheck}>
              {t.recheck}
            </button>
          </div>
        </section>
      )}
    </>
  )
}

const ICON_PROPS = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const

const OK_ICON = (
  <svg {...ICON_PROPS}>
    <circle cx="10" cy="10" r="7.5" />
    <path d="M6.8 10.2l2.2 2.2 4.2-4.6" />
  </svg>
)

const UPDATE_ICON = (
  <svg {...ICON_PROPS}>
    <circle cx="10" cy="10" r="7.5" />
    <path d="M10 6.2v7.2M6.9 10.6L10 13.6l3.1-3" />
  </svg>
)

const INFO_ICON = (
  <svg {...ICON_PROPS}>
    <circle cx="10" cy="10" r="7.5" />
    <path d="M10 9v4.5" />
    <path d="M10 6.4v.2" />
  </svg>
)

const ERROR_ICON = (
  <svg {...ICON_PROPS}>
    <circle cx="10" cy="10" r="7.5" />
    <path d="M10 6v5" />
    <path d="M10 13.6v.2" />
  </svg>
)

const WARN_ICON = (
  <svg {...ICON_PROPS}>
    <path d="M10 3.5l7 12.5H3z" />
    <path d="M10 8.5v3.5" />
    <path d="M10 14.2v.1" />
  </svg>
)
