import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { MicCheckPage } from '../recording/MicCheckPage'
import type { openListeningSession } from '../recording/capture'
import { act, getRound } from './api'
import { writeNoiseFloor } from './session'
import { roundStrings } from './strings'

const t = roundStrings.mic

export function RoundMicPage({ open }: { open?: typeof openListeningSession }) {
  const roundId = Number(useParams().roundId)
  const navigate = useNavigate()
  const [leaving, setLeaving] = useState(false)
  // from "Continue your round?": the round stays as it is unless the learner starts it again
  const resuming = useSearchParams()[0].get('resume') === '1'

  const back = async () => {
    setLeaving(true)
    try {
      const round = await getRound(roundId)
      if (round.status === 'active' || round.status === 'paused') {
        await act(roundId, {
          action: 'abandon',
          from_phase: round.phase,
          ...(round.current_attempt ? { attempt_id: round.current_attempt.id } : {}),
        })
      }
    } catch {
      // a missing or already ended round needs no abandon; go back to the setup either way
    }
    navigate('/practice')
  }

  return (
    <MicCheckPage
      open={open}
      crumbs={
        <nav className="crumbs" aria-label="Breadcrumb">
          <Link to="/practice">{t.crumbSetup}</Link> › {t.crumbHere}
        </nav>
      }
      footer={({ ready, floorDb }) => (
        <div className="actions mt-8">
          <button
            type="button"
            className="btn primary big"
            disabled={!ready || leaving}
            onClick={() => {
              writeNoiseFloor(floorDb)
              navigate(resuming ? `/rounds/${roundId}?resume=1` : `/rounds/${roundId}`)
            }}
          >
            {t.start}
          </button>
          {resuming ? (
            <button type="button" className="btn" onClick={() => navigate('/')}>
              {roundStrings.recovery.notNow}
            </button>
          ) : (
            <button type="button" className="btn" disabled={leaving} onClick={() => void back()}>
              {t.back}
            </button>
          )}
        </div>
      )}
    />
  )
}
