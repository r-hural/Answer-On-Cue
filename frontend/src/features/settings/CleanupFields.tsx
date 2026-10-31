import { useState } from 'react'
import { type CleanupResult, type StorageInfo, saveCleanup } from './storage'
import { settingsStrings } from './strings'

const s = settingsStrings.storage

const KEEP_MAX = 100
const DAYS_MAX = 3650

/** A whole number from 0 to max, as typed. */
const valid = (text: string, max: number) => /^\d+$/.test(text) && Number(text) <= max

/** "Clean-up saved." plus what the new limits deleted at once. */
export function cleanupText(result: CleanupResult): string {
  const parts: string[] = []
  if (result.deleted_backups > 0) parts.push(s.cleanupBackups(result.deleted_backups))
  if (result.deleted_exports > 0) parts.push(s.cleanupExports(result.deleted_exports))
  return parts.length > 0 ? `${s.cleanupSaved} ${s.cleanupDeleted(parts.join(' and '))}` : s.cleanupSaved
}

/** The automatic clean-up limits: pre-restore backups kept and the age of exports. The caller
 * keys it by the saved limits, so the fields start again from what the server holds. */
export function CleanupFields({
  info,
  onSaved,
  onError,
}: {
  info: StorageInfo
  onSaved: (message: string) => void
  onError: (message: string) => void
}) {
  const [keep, setKeep] = useState(String(info.pre_restore_keep))
  const [days, setDays] = useState(String(info.export_keep_days))
  const [saving, setSaving] = useState(false)

  const ok = valid(keep, KEEP_MAX) && valid(days, DAYS_MAX)
  const changed = Number(keep) !== info.pre_restore_keep || Number(days) !== info.export_keep_days

  async function save() {
    setSaving(true)
    try {
      onSaved(cleanupText(await saveCleanup(Number(keep), Number(days))))
    } catch (failure) {
      onError((failure as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h3>{s.cleanupHeading}</h3>
      <p className="small muted">{s.cleanupHelp}</p>
      <div className="field">
        <label className="label" htmlFor="cleanup-keep">
          {s.cleanupKeep}
        </label>
        <input
          className="input"
          id="cleanup-keep"
          type="number"
          min={0}
          max={KEEP_MAX}
          value={keep}
          onChange={(event) => setKeep(event.target.value)}
        />
        <span className="hint">{s.cleanupKeepHint}</span>
      </div>
      <div className="field">
        <label className="label" htmlFor="cleanup-days">
          {s.cleanupDays}
        </label>
        <input
          className="input"
          id="cleanup-days"
          type="number"
          min={0}
          max={DAYS_MAX}
          value={days}
          onChange={(event) => setDays(event.target.value)}
        />
        <span className="hint">{s.cleanupDaysHint}</span>
      </div>
      <div className="actions mt-3">
        <button className="btn" type="button" disabled={saving || !ok || !changed} onClick={() => void save()}>
          {saving ? s.working : s.cleanupSave}
        </button>
      </div>
    </>
  )
}
