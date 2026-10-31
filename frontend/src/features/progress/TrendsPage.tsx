import { useEffect, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { plNbsp } from '../../i18n/polish'
import { Notice } from '../../ui/Notice'
import { metricOrUnavailable, dateOnly, score, seconds, seconds1 } from '../rounds/feedback/format'
import { roundPolicyLabel } from '../rounds/sourcePolicy'
import { ProgressTabs } from './ProgressTabs'
import { progressStrings } from './strings'
import { TrendChart, type ChartMark, type ChartRound } from './TrendChart'
import {
  getTrends,
  type TrendChange,
  type TrendDifficulty,
  type TrendFilter,
  type TrendPoint,
  type TrendSeries,
  type TrendSources,
  type TrendStyle,
  type TrendsOut,
} from './trends'
import {
  PERCENT_AXIS,
  num,
  perMinuteAxis,
  round1,
  scoreAxis,
  secondsAxis,
  tradeoffIndex,
  washes,
  ariaValues,
  type Value,
} from './trendGeometry'

const t = progressStrings.trends

const SERIES: TrendSeries[] = ['main', 'followup', 'immediate', 'retry', 'extension']
const SOURCES: TrendSources[] = ['materials', 'all']
const STYLES: TrendStyle[] = ['short', 'open']
const DIFFICULTIES: TrendDifficulty[] = ['recall', 'apply', 'analysis']
const BAND_NAMES = progressStrings.bandWords

interface SubjectRef {
  id: number
  name: string
}

interface View {
  subjectId: number | null
  sources: TrendSources
  style: TrendStyle | null
  difficulty: TrendDifficulty | null
  series: TrendSeries
  interrupted: boolean
  assisted: boolean
}

function pick<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((item) => item === value) ?? null
}

function viewOf(params: URLSearchParams): View {
  return {
    subjectId: Number(params.get('subject')) || null,
    // All questions by default; an old ?sources=expanded link reads as All questions too
    sources: params.get('sources') === 'materials' ? 'materials' : 'all',
    style: pick(params.get('style'), STYLES),
    difficulty: pick(params.get('difficulty'), DIFFICULTIES),
    series: pick(params.get('questions'), SERIES) ?? 'main',
    interrupted: params.get('interrupted') === '1',
    assisted: params.get('assisted') === '1',
  }
}

/** The request of a view; "answers with help" only has a meaning for the main series. */
function filterOf(view: View & { subjectId: number }): TrendFilter {
  return {
    subjectId: view.subjectId,
    sources: view.sources,
    series: view.series,
    style: view.style,
    difficulty: view.difficulty,
    interrupted: view.interrupted,
    assisted: view.assisted && view.series === 'main',
  }
}

const isDefault = (view: View) =>
  view.sources === 'all' &&
  view.style === null &&
  view.difficulty === null &&
  view.series === 'main' &&
  !view.interrupted &&
  !view.assisted

const dateOf = (point: TrendPoint) => dateOnly(point.started_at)

const per = (value: Value, format: (v: number) => string) => (value === null ? t.unavailable : format(value))

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </>
  )
}

function Readout({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <div className="k">{label}</div>
      <div className="v">{value}</div>
      <p className="note num">{note}</p>
    </div>
  )
}

function Key({ items }: { items: { text: string; kind?: 'dash' | 'dot' | 'mark' | 'bar' }[] }) {
  return (
    <ul className="trend-key" aria-label={t.key}>
      {items.map(({ text, kind }) => (
        <li key={text} className={kind === 'bar' ? undefined : kind}>
          {kind === 'bar' ? (
            <span className="bar-sample" aria-hidden="true" />
          ) : (
            <svg viewBox="0 0 28 10" aria-hidden="true">
              {kind === 'mark' ? <line x1="14" y1="0" x2="14" y2="10" /> : <line x1="1" y1="5" x2="27" y2="5" />}
            </svg>
          )}
          {text}
        </li>
      ))}
    </ul>
  )
}

function bandRanges(bounds: readonly number[], shown: ReturnType<typeof washes>): string {
  const ranges = {
    weak: [0, bounds[0] - 1],
    needs: [bounds[0], bounds[1] - 1],
    good: [bounds[1], bounds[2] - 1],
    'very-good': [bounds[2], 100],
  } as const
  const names = { weak: 'weak', needs: 'needs_improvement', good: 'good', 'very-good': 'very_good' } as const
  return shown
    .map((wash) => `${BAND_NAMES[names[wash.band]]} ${ranges[wash.band][0]}–${ranges[wash.band][1]}`)
    .join(', ')
}

function marksFor(points: TrendPoint[], kinds: TrendChange[]): ChartMark[] {
  const marks: ChartMark[] = []
  points.forEach((point, index) => {
    const found = point.changes.filter((change) => kinds.includes(change))
    if (found.length > 0) marks.push({ index, text: found.map((change) => t.changes[change]).join(' · ') })
  })
  return marks
}

function markKey(marks: ChartMark[], kinds: TrendChange[], points: TrendPoint[]) {
  if (marks.length === 0) return []
  const present = kinds.filter((kind) => points.some((point) => point.changes.includes(kind)))
  return [{ text: present.map((kind) => t.changes[kind]).join(' · '), kind: 'mark' as const }]
}

function pointExtras(point: TrendPoint): string[] {
  const extra: string[] = []
  if (point.status === 'abandoned') extra.push(t.endedEarly)
  if (point.assisted > 0) extra.push(t.withHelpCaption.replace(/\.$/, '').toLowerCase())
  return extra
}

function rangeOf(points: TrendPoint[]): string {
  return t.range(dateOf(points[0]), dateOf(points[points.length - 1]))
}

function Knowledge({ data }: { data: TrendsOut }) {
  const points = data.points
  const [first, latest] = [points[0], points[points.length - 1]]
  const primary = points.map((p) => p.primary)
  const factual = points.map((p) => p.factual)
  const language = points.map((p) => p.language)
  const axis = scoreAxis([...primary, ...factual, ...language])
  const stripes = washes(axis, data.band_bounds)
  const kinds: TrendChange[] = ['band_table', 'model', 'analysis']
  const marks = marksFor(points, kinds)
  const rounds: ChartRound[] = points.map((p) => ({
    id: p.round_id,
    date: dateOf(p),
    count: p.answers,
    title:
      p.primary === null ? null : t.pointTitle(dateOf(p), t.knowledge.primary, score(p.primary), pointExtras(p)),
  }))
  const early = points
    .filter((p) => p.status === 'abandoned')
    .map((p) => t.knowledge.endedEarly(dateOf(p), p.answers))
  const caption = [
    t.knowledge.caption(points.length, bandRanges(data.band_bounds, stripes)),
    early.length > 0 ? `; ${early.join(', ')}. ` : '. ',
    t.knowledge.captionEnd,
    points.some((p) => p.assisted > 0) ? ` ${t.withHelpCaption}` : '',
    points.some((p) => p.unresolved > 0) ? ` ${t.processingCaption}` : '',
  ].join('')
  const marksAria = marks.map((m) => t.knowledge.markAria(dateOf(points[m.index]), m.text)).join('')
  const label = t.knowledge.aria(
    rangeOf(points),
    ariaValues(primary, score, t.unavailable),
    ariaValues(factual, score, t.unavailable),
    ariaValues(language, score, t.unavailable),
    marksAria,
  )
  const note = (firstValue: Value) =>
    firstValue === null ? t.unavailableOn(dateOf(first)) : t.onDate(score(firstValue), dateOf(first))
  return (
    <section className="section" aria-labelledby="h-knowledge">
      <h2 id="h-knowledge">{t.knowledge.title}</h2>
      <div className="cols-main">
        <div className="flow">
          <Key
            items={[
              { text: t.knowledge.primary },
              { text: t.knowledge.factual, kind: 'dash' },
              { text: t.knowledge.language, kind: 'dot' },
              ...markKey(marks, kinds, points),
            ]}
          />
          <TrendChart
            label={label}
            rounds={rounds}
            axis={axis}
            format={String}
            lines={[
              { values: primary, axis },
              { values: factual, axis, style: 'dash' },
              { values: language, axis, style: 'dot soft' },
            ]}
            washes={stripes}
            marks={marks}
            counts
          />
          <p className="trend-cap">{caption}</p>
        </div>
        <div className="scores stacked" aria-label={t.knowledge.latest}>
          <Readout
            label={`${t.knowledge.primary}, ${dateOf(latest)}`}
            value={per(latest.primary, score)}
            note={note(first.primary)}
          />
          <Readout
            label={t.knowledge.factual}
            value={per(latest.factual, score)}
            note={note(first.factual)}
          />
          <Readout
            label={t.knowledge.language}
            value={per(latest.language, score)}
            note={t.answersAssessable(latest.language_assessable)}
          />
        </div>
      </div>
    </section>
  )
}

function Speed({ data, series, interrupted }: { data: TrendsOut; series: TrendSeries; interrupted: boolean }) {
  const points = data.points
  const [first, latest] = [points[0], points[points.length - 1]]
  const total = points.map((p) => p.median_total_response_latency_ms)
  const prep = points.map((p) => p.median_preparation_used_ms)
  const start = points.map((p) => p.median_speech_start_latency_ms)
  const axis = secondsAxis([...total, ...prep, ...start])
  const secs = (ms: Value): Value => (ms === null ? null : ms / 1000)
  const factual = points.map((p) => p.factual)
  const kinds: TrendChange[] = ['analysis']
  const marks = marksFor(points, kinds)
  const trade = tradeoffIndex(points.map((p) => ({ latencyMs: p.median_total_response_latency_ms, factual: p.factual })))
  const rounds: ChartRound[] = points.map((p) => ({
    id: p.round_id,
    date: dateOf(p),
    title:
      p.median_total_response_latency_ms === null
        ? null
        : t.pointTitle(dateOf(p), t.speed.total, seconds1(p.median_total_response_latency_ms), pointExtras(p)),
  }))
  const seconds1List = (values: Value[]) => ariaValues(values.map(secs), (v) => String(round1(v)), t.unavailable)
  const label = t.speed.aria(
    rangeOf(points),
    seconds1List(total),
    seconds1List(prep),
    seconds1List(start),
    ariaValues(factual, score, t.unavailable),
    trade === null ? '' : t.speed.tradeoffAria(dateOf(points[trade])),
  )
  const caption = [
    t.speed.caption(t.seriesPhrase[series]),
    trade === null ? '' : ` ${t.speed.tradeoffCaption(dateOf(points[trade]))}`,
    !interrupted && points.some((p) => p.restarted > 0) ? ` ${t.restartedCaption}` : '',
    points.some((p) => p.assisted > 0) ? ` ${t.withHelpCaption}` : '',
  ].join('')
  const firstTotal = first.median_total_response_latency_ms
  const sec = (ms: number | null) => metricOrUnavailable(ms, undefined, seconds)
  return (
    <section className="section" aria-labelledby="h-speed">
      <h2 id="h-speed">{t.speed.title}</h2>
      <div className="cols-main">
        <div className="flow">
          <Key
            items={[
              { text: t.speed.total },
              { text: t.speed.preparation, kind: 'dash' },
              { text: t.speed.start, kind: 'dot' },
              { text: t.speed.bars, kind: 'bar' },
              ...markKey(marks, kinds, points),
            ]}
          />
          <TrendChart
            label={label}
            rounds={rounds}
            axis={axis}
            format={(tick) => `${num(tick)} s`}
            lines={[
              { values: total.map(secs), axis },
              { values: prep.map(secs), axis, style: 'dash' },
              { values: start.map(secs), axis, style: 'dot soft' },
            ]}
            bars={{ values: factual, axis: PERCENT_AXIS }}
            rightAxis={{ axis: PERCENT_AXIS, format: String }}
            marks={marks}
            note={trade === null ? undefined : { index: trade, text: t.speed.tradeoff }}
          />
          <p className="trend-cap">{caption}</p>
        </div>
        <dl className="facts stacked num">
          <Fact term={t.speed.totalOn(dateOf(latest))}>
            {sec(latest.median_total_response_latency_ms)}
            {firstTotal !== null && latest !== first && (
              <>
                {' '}
                <small>{t.onDate(seconds(firstTotal), dateOf(first))}</small>
              </>
            )}
          </Fact>
          <Fact term={t.speed.preparation}>{sec(latest.median_preparation_used_ms)}</Fact>
          <Fact term={t.speed.start}>
            {metricOrUnavailable(latest.median_speech_start_latency_ms, undefined, seconds1)}{' '}
            <small>{t.answersCount(latest.speed_answers)}</small>
          </Fact>
          {series === 'main' && (
            <Fact term={t.speed.followUps}>
              {t.speed.shownSeparately} <small>{t.speed.chooseFollowUps}</small>
            </Fact>
          )}
        </dl>
      </div>
    </section>
  )
}

const percent = (ratio: number) => `${Math.round(ratio * 100)}%`

function Pauses({ data }: { data: TrendsOut }) {
  const points = data.points
  const [first, latest] = [points[0], points[points.length - 1]]
  const pauses = points.map((p) => p.long_pauses_per_min)
  const filler = points.map((p) => p.filler_per_min)
  const silence = points.map((p) => (p.silence_ratio === null ? null : p.silence_ratio * 100))
  const axis = perMinuteAxis([...pauses, ...filler])
  const kinds: TrendChange[] = ['analysis']
  const marks = marksFor(points, kinds)
  const rounds: ChartRound[] = points.map((p) => ({
    id: p.round_id,
    date: dateOf(p),
    title:
      p.long_pauses_per_min === null
        ? null
        : t.pointTitle(dateOf(p), t.pauses.pointLabel, num(p.long_pauses_per_min), pointExtras(p)),
  }))
  const reliable = points.filter((p) => p.filler_reliable > 0).length
  const label = t.pauses.aria(
    rangeOf(points),
    ariaValues(pauses, num, t.unavailable),
    ariaValues(filler, num, t.unavailable),
    ariaValues(silence, (v) => `${Math.round(v)}%`, t.unavailable),
  )
  return (
    <section className="section" aria-labelledby="h-pauses">
      <h2 id="h-pauses">{t.pauses.title}</h2>
      <div className="cols-main">
        <div className="flow">
          <Key
            items={[
              { text: t.pauses.long },
              { text: t.pauses.filler, kind: 'dash' },
              { text: t.pauses.silence, kind: 'bar' },
              ...markKey(marks, kinds, points),
            ]}
          />
          <TrendChart
            label={label}
            rounds={rounds}
            axis={axis}
            format={num}
            lines={[
              { values: pauses, axis },
              { values: filler, axis, style: 'dash' },
            ]}
            bars={{ values: silence, axis: PERCENT_AXIS }}
            rightAxis={{ axis: PERCENT_AXIS, format: (tick) => `${tick}%` }}
            marks={marks}
          />
          <p className="trend-cap">{t.pauses.caption}</p>
        </div>
        <dl className="facts stacked num">
          <Fact term={t.pauses.longOn(dateOf(latest))}>
            {metricOrUnavailable(latest.long_pauses_per_min, undefined, num)}
            {first.long_pauses_per_min !== null && latest !== first && (
              <>
                {' '}
                <small>{t.onDate(num(first.long_pauses_per_min), dateOf(first))}</small>
              </>
            )}
          </Fact>
          <Fact term={t.pauses.silenceFact}>
            {metricOrUnavailable(latest.silence_ratio, undefined, percent)}
            {first.silence_ratio !== null && latest !== first && (
              <>
                {' '}
                <small>{t.onDate(percent(first.silence_ratio), dateOf(first))}</small>
              </>
            )}
          </Fact>
          <Fact term={t.pauses.fillerFact}>
            {latest.filler_per_min === null ? t.unavailable : t.pauses.perMinute(num(latest.filler_per_min))}{' '}
            <small>
              {t.pauses.lowerBound} · {t.pauses.reliable(reliable, points.length)}
            </small>
          </Fact>
        </dl>
      </div>
    </section>
  )
}

function Fluency({ data }: { data: TrendsOut }) {
  const points = data.points
  const [first, latest] = [points[0], points[points.length - 1]]
  const values = points.map((p) => p.fluency)
  const axis = scoreAxis(values)
  const kinds: TrendChange[] = ['band_table', 'model', 'analysis']
  const marks = marksFor(points, kinds)
  const rounds: ChartRound[] = points.map((p) => ({
    id: p.round_id,
    date: dateOf(p),
    count: p.fluency_assessable,
    title: p.fluency === null ? null : t.pointTitle(dateOf(p), t.fluency.title, score(p.fluency), pointExtras(p)),
  }))
  const label = t.fluency.aria(
    rangeOf(points),
    ariaValues(values, score, t.unavailable),
    points.map((p) => p.fluency_assessable).join(', '),
  )
  const readout = (point: TrendPoint) => (
    <Readout
      key={point.round_id}
      label={t.fluency.on(dateOf(point))}
      value={point.fluency === null ? t.unavailable : score(point.fluency)}
      note={point.fluency === null ? t.fluency.notReliable : t.answersAssessable(point.fluency_assessable)}
    />
  )
  return (
    <section className="section" aria-labelledby="h-fluency">
      <h2 id="h-fluency">{t.fluency.title}</h2>
      <div className="cols-main">
        <div className="flow">
          <Key items={[{ text: t.fluency.average }, ...markKey(marks, kinds, points)]} />
          <TrendChart
            label={label}
            rounds={rounds}
            axis={axis}
            format={String}
            lines={[{ values, axis }]}
            marks={marks}
            counts
          />
          <p className="trend-cap">{t.fluency.caption}</p>
        </div>
        <div className="scores stacked" aria-label={t.fluency.label}>
          {readout(latest)}
          {latest !== first && readout(first)}
        </div>
      </div>
    </section>
  )
}

function Charts({ data, view }: { data: TrendsOut; view: View }) {
  return (
    <>
      <Knowledge data={data} />
      <Speed data={data} series={view.series} interrupted={view.interrupted} />
      <Pauses data={data} />
      <Fluency data={data} />
      <p className="note measure-text muted section">{t.closing}</p>
    </>
  )
}

function NoData({ data, view }: { data: TrendsOut; view: View }) {
  const [only] = data.points
  const date = only ? dateOf(only) : ''
  const subject = view.subjectId ?? data.subject_id
  const practise = `/practice?subject=${subject}&sources=${view.sources}`
  const axis = scoreAxis([only?.primary ?? null])
  return (
    <section className="empty section mt-8" aria-labelledby="h-empty">
      <h2 id="h-empty">
        {t.empty.title} „<span className="content">{plNbsp(data.subject_name)}</span>”
      </h2>
      <p>{t.empty.text(data.points.length)}</p>
      {only && only.primary !== null && (
        <>
          <TrendChart
            label={t.empty.aria(date, score(only.primary))}
            rounds={[
              {
                id: only.round_id,
                date,
                title: t.pointTitle(date, t.knowledge.primary, score(only.primary), pointExtras(only)),
              },
            ]}
            axis={axis}
            format={String}
            lines={[{ values: [only.primary], axis }]}
          />
          <p className="trend-cap mt-2">{t.empty.caption(date)}</p>
        </>
      )}
      <div className="actions mt-6">
        <Link className="btn primary" to={practise}>
          {t.empty.start}
        </Link>
        {only && (
          <Link className="btn" to={`/rounds/${only.round_id}/summary`}>
            {t.empty.seeRound(date)}
          </Link>
        )}
        {!isDefault(view) && (
          <Link className="btn" to={`?subject=${subject}`}>
            {t.empty.clear}
          </Link>
        )}
      </div>
    </section>
  )
}

/** Progress → Trends: one point per round for one subject and source policy. */
export function TrendsPage() {
  const [params, setParams] = useSearchParams()
  const view = viewOf(params)
  const [subjects, setSubjects] = useState<SubjectRef[] | null>(null)
  const [loaded, setLoaded] = useState<{ key: string; data: TrendsOut } | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const { subjectId } = view
  const key = subjectId === null ? null : JSON.stringify(filterOf({ ...view, subjectId }))

  useEffect(() => {
    let cancelled = false
    apiGet<SubjectRef[]>('/subjects')
      .then((list) => !cancelled && setSubjects(list))
      .catch(() => !cancelled && setFailed('subjects'))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (subjectId === null && subjects && subjects.length > 0) {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('subject', String(subjects[0].id))
          return next
        },
        { replace: true },
      )
    }
  }, [subjectId, subjects, setParams])

  useEffect(() => {
    if (subjectId === null || key === null) return
    let cancelled = false
    getTrends(filterOf({ ...view, subjectId }))
      .then((data) => {
        if (cancelled) return
        setLoaded({ key, data })
        setFailed(null)
      })
      .catch(() => !cancelled && setFailed(key))
    return () => {
      cancelled = true
    }
    // `view` is derived from the same inputs as `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const set = (name: string, value: string | null) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value === null || value === '') next.delete(name)
      else next.set(name, value)
      return next
    })

  const data = loaded && loaded.key === key ? loaded.data : null
  const error = failed !== null && (failed === 'subjects' || failed === key)
  const noSubjects = subjects !== null && subjects.length === 0
  const assistedOff = view.series !== 'main'

  return (
    <>
      <div className="page-head mb-6">
        <h1>{progressStrings.title}</h1>
      </div>

      <ProgressTabs current="trends" subjectId={subjectId} />

      {subjects && subjects.length > 0 && (
        <form aria-label={t.filtersLabel} onSubmit={(event) => event.preventDefault()}>
          <div className="filters trend-filters">
            <div className="field">
              <label className="label" htmlFor="f-subject">
                {t.subject}
              </label>
              <select
                className="select content"
                id="f-subject"
                value={subjectId ?? ''}
                onChange={(event) => set('subject', event.target.value)}
              >
                {subjects.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="f-sources">
                {t.sources}
              </label>
              <select
                className="select"
                id="f-sources"
                value={view.sources}
                onChange={(event) => set('sources', event.target.value === 'all' ? null : event.target.value)}
              >
                {SOURCES.map((policy) => (
                  <option key={policy} value={policy}>
                    {roundPolicyLabel(policy)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="f-style">
                {t.style}
              </label>
              <select
                className="select"
                id="f-style"
                value={view.style ?? ''}
                onChange={(event) => set('style', event.target.value)}
              >
                <option value="">{t.styles.all}</option>
                {STYLES.map((style) => (
                  <option key={style} value={style}>
                    {t.styles[style]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="f-difficulty">
                {t.difficulty}
              </label>
              <select
                className="select"
                id="f-difficulty"
                value={view.difficulty ?? ''}
                onChange={(event) => set('difficulty', event.target.value)}
              >
                <option value="">{t.difficulties.all}</option>
                {DIFFICULTIES.map((difficulty) => (
                  <option key={difficulty} value={difficulty}>
                    {t.difficulties[difficulty]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="f-questions">
                {t.questions}
              </label>
              <select
                className="select"
                id="f-questions"
                value={view.series}
                onChange={(event) => set('questions', event.target.value === 'main' ? null : event.target.value)}
              >
                {SERIES.map((series) => (
                  <option key={series} value={series}>
                    {t.series[series]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="actions filter-checks">
            <label className="check">
              <input
                type="checkbox"
                checked={view.interrupted}
                onChange={(event) => set('interrupted', event.target.checked ? '1' : null)}
              />{' '}
              {t.interrupted}
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={view.assisted}
                disabled={assistedOff}
                aria-describedby={assistedOff ? 'assisted-hint' : undefined}
                onChange={(event) => set('assisted', event.target.checked ? '1' : null)}
              />{' '}
              {t.assisted}
            </label>
            <span className="hint">{t.filtersHint}</span>
            {assistedOff && (
              <span className="hint" id="assisted-hint">
                {t.assistedOnlyMain}
              </span>
            )}
          </div>
        </form>
      )}

      {error && (
        <Notice kind="error" className="mt-6">
          {t.loadError}
        </Notice>
      )}
      {!error && !noSubjects && !data && (
        <p role="status" className="mt-6">
          {t.loading}
        </p>
      )}

      {noSubjects && (
        <section className="empty section" aria-labelledby="h-empty">
          <h2 id="h-empty">{t.empty.noSubjectsTitle}</h2>
          <p>{t.empty.noSubjectsText}</p>
          <Link className="btn" to="/library">
            {t.empty.toLibrary}
          </Link>
        </section>
      )}

      {data && data.points.length >= 2 && <Charts data={data} view={view} />}
      {data && data.points.length < 2 && <NoData data={data} view={view} />}
    </>
  )
}
