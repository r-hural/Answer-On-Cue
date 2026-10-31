import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { type ExtensionOffer, GIVEN_UP, type RetryOffer } from '../api'
import { roundStrings } from '../strings'
import type { ItemRow, RoundReport } from '../reports'
import { bandClass, bandKey, BANDS, dateTime, metricOrUnavailable, score, seconds, seconds1 } from '../feedback/format'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.summary
const a = roundStrings.adaptive
const r = roundStrings.repetition
const bandWords = roundStrings.exam.feedback.bands

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Fragment>
      <dt>{label}</dt>
      <dd className="num">{children}</dd>
    </Fragment>
  )
}

const value = (n: number | null | undefined) => metricOrUnavailable(n, undefined, score)

/** Knowledge and language: the round score, the component averages and the band distribution. */
export function KnowledgePanel({ report }: { report: RoundReport }) {
  const knowledge = report.knowledge
  if (!knowledge) return null
  const early = report.status === 'abandoned'
  const assessed = report.items.filter((item) => item.status === 'ready' && item.outcome !== GIVEN_UP).length
  const notAssessed = knowledge.not_assessed ?? 0
  const distribution = knowledge.band_distribution
  const parts = BANDS.map((band) => `${distribution[band]} ${bandWords[band]}`).join(', ')
  return (
    <section className="panel flow" aria-labelledby="h-knowledge">
      <div className="section-head">
        <h2 id="h-knowledge">{t.knowledge}</h2>
        {report.provisional && <span className="chip warn">{t.provisional}</span>}
      </div>
      <p className="headline">
        <b>{knowledge.primary_score === null ? metricOrUnavailable(null) : score(knowledge.primary_score)}</b>{' '}
        <span className="small muted">
          {report.provisional || early
            ? t.assessedOf(assessed, report.asked, report.total - report.asked)
            : t.average(report.asked - notAssessed)}
        </span>
      </p>
      {notAssessed > 0 && <p className="note">{t.notAssessedCount(notAssessed)}</p>}
      <dl className="facts">
        <Fact label={t.correctness}>{value(knowledge.mean_correctness)}</Fact>
        <Fact label={t.completeness}>{value(knowledge.mean_completeness)}</Fact>
        <Fact label={t.factual}>{value(knowledge.mean_factual)}</Fact>
        <Fact label={t.language}>
          {value(knowledge.mean_language)} <small>{t.assessable(knowledge.language_assessable)}</small>
        </Fact>
      </dl>
      <h3>{t.bands}</h3>
      {BANDS.some((band) => distribution[band] > 0) && (
        <div className="band-bar" role="img" aria-label={t.bandsAria(parts)}>
          {BANDS.filter((band) => distribution[band] > 0).map((band) => (
            <span key={band} className={bandKey(band)} style={{ flexGrow: distribution[band] }}>
              {distribution[band]}
            </span>
          ))}
        </div>
      )}
      <ul className="band-list" aria-label={t.bandsLabel}>
        {BANDS.map((band) => (
          <li key={band}>
            <span className={bandClass(band)}>
              {distribution[band]} {bandWords[band]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The follow-ups' own series: asked, their average and Good or better; never the round score. */
export function FollowUpsBox({ report }: { report: RoundReport }) {
  const series = report.follow_ups
  if (!series) return null
  const factual = series.knowledge.mean_factual
  return (
    <section className="box tint flow" aria-labelledby="h-followups">
      <div className="section-head">
        <h2 id="h-followups">{t.followUps}</h2>
        {series.provisional && <span className="chip warn">{t.provisional}</span>}
      </div>
      <p>
        {t.followUpsLine(series.asked, factual === null ? null : score(factual), series.good_or_better)}
        {series.not_asked > 0 ? ` · ${t.followUpsNotAsked(series.not_asked)}` : ''}
      </p>
      <p className="small muted">{t.followUpsApart}</p>
    </section>
  )
}

/** Speed and fluency: medians and rates; what cannot be measured says "unavailable", never 0. */
export function SpeedSummary({ report }: { report: RoundReport }) {
  const delivery = report.delivery
  const knowledge = report.knowledge
  if (!delivery || !knowledge) return null
  const withStart = report.items.filter((item: ItemRow) => item.speech_start_latency_ms !== null).length
  const target = delivery.target_attainment.speech_start_latency_ms
  return (
    <section className="panel flow" aria-labelledby="h-speed">
      <div className="section-head">
        <h2 id="h-speed">{t.speed}</h2>
        {report.provisional && <span className="chip warn">{t.provisional}</span>}
      </div>
      <dl className="facts">
        <Fact label={t.prepMedian}>{metricOrUnavailable(delivery.median_preparation_used_ms, undefined, seconds)}</Fact>
        <Fact label={t.startMedian}>
          {metricOrUnavailable(delivery.median_speech_start_latency_ms, undefined, seconds1)}
          {delivery.median_speech_start_latency_ms !== null && <small>{t.answersCount(withStart)}</small>}
        </Fact>
        <Fact label={t.totalBefore}>
          {metricOrUnavailable(delivery.median_total_response_latency_ms, undefined, seconds)}
          {delivery.median_total_response_latency_successful_ms !== null && (
            <small>{t.totalGood(seconds(delivery.median_total_response_latency_successful_ms))}</small>
          )}
        </Fact>
        <Fact label={t.targets}>{target && target[1] > 0 ? t.targetsValue(target[0], target[1]) : metricOrUnavailable(null)}</Fact>
        <Fact label={t.longestPause}>{metricOrUnavailable(delivery.median_longest_pause_ms, undefined, seconds1)}</Fact>
        <Fact label={t.pausesPerMin}>{metricOrUnavailable(delivery.long_pauses_per_speaking_min)}</Fact>
        <Fact label={t.silence}>
          {metricOrUnavailable(delivery.aggregate_silence_ratio, undefined, (ratio) => `${Math.round(ratio * 100)}%`)}
        </Fact>
        <Fact label={t.fluency}>
          {value(knowledge.mean_fluency)} <small>{t.assessable(knowledge.fluency_assessable)}</small>
        </Fact>
      </dl>
    </section>
  )
}

/** The adaptive extension's own series: asked, its average and Good or better; never the round score. */
export function ExtensionBox({ report }: { report: RoundReport }) {
  const series = report.extension
  if (!series) return null
  const factual = series.knowledge?.mean_factual ?? null
  return (
    <section className="box tint flow" aria-labelledby="h-extension">
      <div className="section-head">
        <h2 id="h-extension">{a.extension}</h2>
        {series.provisional && <span className="chip warn">{t.provisional}</span>}
      </div>
      <p>
        {series.withheld
          ? a.running
          : t.followUpsLine(series.asked, factual === null ? null : score(factual), series.good_or_better)}
      </p>
      <p className="small muted">{t.followUpsApart}</p>
    </section>
  )
}

function Topics({ offer }: { offer: ExtensionOffer }) {
  const cl = useContentLanguage()
  return (
    <>
      {offer.topics.map((topic, n) => (
        <Fragment key={topic.topic_id}>
          {n > 0 && ', '}
          <span className="content" lang={cl.bcp47}>
            {cl.typeset(topic.title)}
          </span>
        </Fragment>
      ))}
    </>
  )
}

export interface ExtensionActions {
  /** Whether the extension's action is the screen's one primary button. */
  primary: boolean
  busy: boolean
  /** The generation poll ran out of time: offer a manual check. */
  slow: boolean
  onStart: () => void
  onGenerate: () => void
  onCheck: () => void
}

/** Whether the extension has a column in "Keep practising". */
export const extensionShown = (offer: ExtensionOffer | null): offer is ExtensionOffer =>
  offer !== null && offer.status !== 'off' && offer.status !== 'ended_early' && offer.status !== 'not_finished'

/** Whether the extension's column has a start or generate button (the one that may be primary). */
export function extensionHasAction(offer: ExtensionOffer | null): boolean {
  if (!extensionShown(offer) || ['waiting', 'no_weak', 'started'].includes(offer.status)) return false
  return offer.available > 0 || offer.status === 'no_questions'
}

/** The end-of-round adaptive extension's column of "Keep practising", as the offer stands now. */
export function ExtensionColumn({ offer, subjectId, actions }: { offer: ExtensionOffer; subjectId: number; actions: ExtensionActions }) {
  if (!extensionShown(offer)) return null
  const main = actions.primary ? 'btn primary' : 'btn'
  const preparing = offer.status === 'generating' && (
    <>
      <p role="status">{actions.slow ? a.slow : a.preparing}</p>
      {actions.slow && (
        <div className="actions">
          <button type="button" className="btn small" onClick={actions.onCheck}>
            {a.checkAgain}
          </button>
        </div>
      )}
    </>
  )
  let body: ReactNode
  if (offer.status === 'waiting') body = <p>{a.waiting}</p>
  else if (offer.status === 'no_weak') body = <p>{a.noWeak}</p>
  else if (offer.status === 'started') {
    const child = offer.extension_round_id as number
    const open = offer.extension_status === 'active' || offer.extension_status === 'paused'
    body = (
      <div className="actions">
        <Link className="btn" to={open ? `/rounds/${child}` : `/rounds/${child}/summary`}>
          {open ? a.continue : a.summary}
        </Link>
      </div>
    )
  } else if (offer.available > 0) {
    const short = offer.available < offer.requested
    body = (
      <>
        <p>
          {a.offer(offer.available)} (<Topics offer={offer} />). {a.apart}
        </p>
        {short && <p className="note">{a.onlyInBank(offer.available)}</p>}
        {preparing}
        <div className="actions">
          <button type="button" className={main} disabled={actions.busy} onClick={actions.onStart}>
            {actions.busy ? a.starting : a.start(offer.available)}
          </button>
          {short && offer.status === 'ready' && (
            <button type="button" className="btn" disabled={actions.busy} onClick={actions.onGenerate}>
              {a.generateMore}
            </button>
          )}
        </div>
      </>
    )
  } else {
    body = (
      <>
        <p>
          {a.noQuestions} (<Topics offer={offer} />).
        </p>
        {(offer.needs_review ?? 0) > 0 && (
          <p className="note">
            {a.needsReview} <Link to={`/library/${subjectId}/questions`}>{a.toBank}</Link>
          </p>
        )}
        {preparing}
        {offer.status === 'no_questions' && (
          <div className="actions">
            <button type="button" className={main} disabled={actions.busy} onClick={actions.onGenerate}>
              {a.generateNew}
            </button>
          </div>
        )}
      </>
    )
  }
  return (
    <div className="flow">
      <h3>{a.extension}</h3>
      {body}
    </div>
  )
}

/** Whether the retry round has a column in "Keep practising". */
export const retryShown = (offer: RetryOffer | null): offer is RetryOffer =>
  offer !== null && ['ready', 'waiting', 'extension_open', 'none_below', 'started'].includes(offer.status)

export interface RetryActions {
  /** Whether the retry round's start is the screen's one primary button. */
  primary: boolean
  busy: boolean
  onStart: () => void
}

/** The end-of-round retry round's column of "Keep practising": the answers below Good, once more. */
export function RetryColumn({ offer, actions }: { offer: RetryOffer; actions: RetryActions }) {
  if (!retryShown(offer)) return null
  let body: ReactNode
  if (offer.status === 'ready') {
    body = (
      <>
        <p>{r.retryOffer(offer.count)}</p>
        <div className="actions">
          <button type="button" className={actions.primary ? 'btn primary' : 'btn'} disabled={actions.busy} onClick={actions.onStart}>
            {actions.busy ? r.starting : r.startRetry(offer.count)}
          </button>
        </div>
      </>
    )
  } else if (offer.status === 'started') {
    const child = offer.retry_round_id as number
    const open = offer.retry_status === 'active' || offer.retry_status === 'paused'
    body = (
      <div className="actions">
        <Link className="btn" to={open ? `/rounds/${child}` : `/rounds/${child}/summary`}>
          {open ? r.continueRetry : r.retrySummary}
        </Link>
      </div>
    )
  } else {
    body = <p>{offer.status === 'waiting' ? r.retryWaiting : offer.status === 'extension_open' ? r.retryAfterExtension : r.noneBelow}</p>
  }
  return (
    <div className="flow">
      <h3>{r.retryRound}</h3>
      {body}
      {offer.unresolved > 0 && offer.status !== 'started' && <p className="note">{r.retryUnresolved(offer.unresolved)}</p>}
    </div>
  )
}

/** "Keep practising": the adaptive extension and the retry round, side by side; nothing when neither is offered. */
export function KeepPractisingPanel({ extension, retry }: { extension: ReactNode; retry: ReactNode }) {
  if (!extension && !retry) return null
  return (
    <section className="panel" aria-labelledby="h-keep">
      <h2 id="h-keep">{a.keep}</h2>
      <div className="cols-2 mt-6">
        {extension}
        {retry}
      </div>
    </section>
  )
}

/** The Answer again repeats: how many, the average factual before and after, Good or better; never the round score. */
export function RepeatBox({ report }: { report: RoundReport }) {
  const series = report.repeats
  if (!series) return null
  const before = series.rows.map((row) => row.original.factual).filter((v): v is number => v !== null)
  const after = series.knowledge.mean_factual
  const mean = before.length > 0 ? before.reduce((sum, v) => sum + v, 0) / before.length : null
  return (
    <section className="box tint flow" aria-labelledby="h-again">
      <h2 id="h-again">{r.againTitle}</h2>
      <p className="num">{r.repeatLine(series.asked, mean === null ? null : score(mean), after === null ? null : score(after), series.good_or_better)}</p>
      <p className="small muted">{r.repeatApart}</p>
    </section>
  )
}

/** The round's retry round (round.html h-retry): a link to its summary and its own score; never this round's. */
export function RetryBox({ report }: { report: RoundReport }) {
  const series = report.retry
  if (!series) return null
  const own = series.knowledge?.primary_score ?? null
  const mine = report.knowledge?.primary_score ?? null
  return (
    <section className="box tint flow" aria-labelledby="h-retry">
      <h2 id="h-retry">{r.retryRound}</h2>
      <ul className="rows compact">
        <li>
          <div>
            <Link className="title num" to={`/rounds/${series.round_id}/summary`}>
              {dateTime(series.started_at)}
            </Link>
            <div className="meta num">
              <span>{r.retryQuestions(series.total)}</span>
              {series.withheld ? <span>{r.retryRunning}</span> : own !== null && <span>{r.retryScore(score(own))}</span>}
            </div>
          </div>
        </li>
      </ul>
      <p className="small">{r.retryKeeps(mine === null ? null : score(mine))}</p>
    </section>
  )
}
