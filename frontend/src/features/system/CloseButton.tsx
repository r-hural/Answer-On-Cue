import { useState } from 'react'
import { appStrings } from '../../i18n/app'
import { getInfo, postShutdown } from './api'
import { systemStrings } from './strings'

export function CloseButton() {
  const [closed, setClosed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleClick() {
    setError(null)
    setBusy(true)
    try {
      const info = await getInfo()
      const warning = info.running_jobs > 0 ? `\n${systemStrings.runningJobs(info.running_jobs)}` : ''
      if (!window.confirm(`${systemStrings.confirmClose}${warning}`)) return
      await postShutdown()
      setClosed(true)
    } catch {
      setError(systemStrings.closeFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {error && (
        <span role="alert" className="small muted">
          {error}
        </span>
      )}
      <button type="button" className="btn small" onClick={handleClick} disabled={busy}>
        {systemStrings.close}
      </button>
      {closed && (
        <div role="status" className="closed-screen">
          <div className="shell focus">
            <div className="shell-in">
              <span className="brand">{appStrings.appName}</span>
              <span className="chip">
                <span className="dot" aria-hidden="true" />
                {systemStrings.notRunning}
              </span>
            </div>
          </div>
          <div className="page narrow">
            <section className="panel" aria-labelledby="closed-h">
              <h1 id="closed-h">{systemStrings.closedTitle}</h1>
              <div className="closed-row">
                <p className="lede">{systemStrings.closed}</p>
                <figure className="shortcut">
                  <svg viewBox="0 0 56 56" aria-hidden="true">
                    <rect x="2" y="2" width="52" height="52" rx="12" className="ic-tile" />
                    <path
                      d="M16 20.5a4.5 4.5 0 0 1 4.5-4.5h15a4.5 4.5 0 0 1 4.5 4.5v9a4.5 4.5 0 0 1-4.5 4.5H25l-6 5v-5.2a4.5 4.5 0 0 1-3-4.3z"
                      className="ic-mark"
                      strokeWidth="2.4"
                      strokeLinejoin="round"
                    />
                    <path d="M23 25h10" className="ic-mark" strokeWidth="2.4" strokeLinecap="round" />
                    <rect x="3" y="37" width="16" height="16" rx="3" className="ic-badge" />
                    <path
                      d="M8 48.5c0-4 2.2-6 6-6M11.5 40l2.8 2.5-2.8 2.5"
                      className="ic-arrow"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <figcaption>{appStrings.appName}</figcaption>
                </figure>
              </div>
            </section>
          </div>
        </div>
      )}
    </>
  )
}
