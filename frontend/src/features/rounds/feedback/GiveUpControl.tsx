import { useState } from 'react'
import { roundStrings } from '../strings'

const t = roundStrings.review

/**
 * "Give up this answer" in two steps, inline: the button, then the consequence with
 * "Give up" and "Cancel" in its place.
 */
export function GiveUpControl({
  onGiveUp,
  busy = false,
  disabled = false,
}: {
  onGiveUp: () => void
  busy?: boolean
  disabled?: boolean
}) {
  const [asking, setAsking] = useState(false)
  if (!asking || busy) {
    return (
      <button type="button" className="btn small" disabled={disabled || busy} onClick={() => setAsking(true)}>
        {busy ? t.givingUp : t.giveUp}
      </button>
    )
  }
  return (
    <>
      <p className="small">{t.giveUpConfirm}</p>
      <button
        type="button"
        className="btn small danger"
        onClick={() => {
          setAsking(false)
          onGiveUp()
        }}
      >
        {t.giveUpYes}
      </button>
      <button type="button" className="btn small" autoFocus onClick={() => setAsking(false)}>
        {t.giveUpCancel}
      </button>
    </>
  )
}
