import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, apiGet } from '../../api/client'
import { plNbsp } from '../../i18n/polish'
import { Notice, svgProps } from '../../ui/Notice'
import { settingsStrings } from '../settings/strings'
import { browserLang, speakSample } from '../settings/browserVoice'
import { type ContentLanguage, listLanguages } from '../library/api'
import { fetchPreferences, fetchVoices, previewVoice, type Preferences, type VoiceInfo } from '../settings/api'
import { createRound } from './api'
import { prefetchRound } from './questionAudio'
import { type RoundEstimate, fetchRoundEstimate } from '../costs/api'
import { monthName, usd, usdRange } from '../costs/format'
import { providerLabel } from '../costs/strings'
import type { DifficultyChoice, FeedbackTiming, ScopeKind, StyleChoice } from './api'
import { roundTypeLabel } from './roundType'
import { roundStrings } from './strings'
import { roundPolicyLabel } from './sourcePolicy'
import {
  availableCount,
  ANSWER_RANGE,
  defaultForm,
  estimateMinutes,
  EXTENSION_RANGE,
  invalidTimers,
  PREP_RANGE,
  readStored,
  setupConflict,
  setupNotice,
  STORAGE_KEY,
  toRoundCreate,
  type AudioChoice,
  type QuestionFacts,
  type SetupForm,
} from './setup'

const t = roundStrings.setup
const a = roundStrings.adaptive
const r = roundStrings.repetition
const ch = roundStrings.chapters
const c = roundStrings.costs
const sv = roundStrings.sourcesV2
/** A voice the server reads (and caches): it has a voice list and a server preview. */
const premium = (audio: AudioChoice) =>
  audio === 'piper' || audio === 'azure_speech' || audio === 'openai'

interface SubjectRef {
  id: number
  name: string
  /** Missing on an older server: one chapter. */
  chapter_count?: number
  /** The content language code; missing on an older server: Polish. */
  language?: string
}
interface ChapterRef {
  id: number
  name: string | null
}
interface TopicNode {
  id: number
  title: string
  status: string
  question_count: number
  children?: TopicNode[]
  chapter_id?: number | null
}
interface DocumentRef {
  id: number
  title: string
  latest_version: { passage_count: number | null }
  chapter_id?: number | null
}
interface SubjectData {
  subjectId: number
  topics: { topic: TopicNode; child: boolean }[]
  documents: DocumentRef[]
  questions: QuestionFacts[]
}

const VOICE_MEMORY_KEY = 'aoc.practiceVoice'
type VoiceMemory = Record<string, { audio: AudioChoice; voice: string; rate: number }>

/** The audio choice of this tab, per content language: a Polish choice never carries over to English. */
function readMemory(): VoiceMemory {
  try {
    return JSON.parse(sessionStorage.getItem(VOICE_MEMORY_KEY) ?? '{}') as VoiceMemory
  } catch {
    return {}
  }
}

function writeMemory(language: string, form: SetupForm): void {
  try {
    const memory = readMemory()
    memory[language] = { audio: form.audio, voice: form.voice, rate: form.rate }
    sessionStorage.setItem(VOICE_MEMORY_KEY, JSON.stringify(memory))
  } catch {
    // storage unavailable: the choice is simply not remembered
  }
}

function writeStored(form: SetupForm): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(form))
  } catch {
    // storage unavailable: the form simply starts fresh next time
  }
}

function flatten(topics: TopicNode[]): SubjectData['topics'] {
  const rows: SubjectData['topics'] = []
  for (const topic of topics) {
    if (topic.status !== 'active') continue
    rows.push({ topic, child: false })
    for (const child of topic.children ?? []) {
      // a child is in its parent's chapter
      if (child.status === 'active') rows.push({ topic: { ...child, chapter_id: child.chapter_id ?? topic.chapter_id }, child: true })
    }
  }
  return rows
}

/** Adopts a chapter of the list: the wanted one, else the current one, else the first; another chapter's choices are cleared. */
function adoptChapter(current: SetupForm, list: ChapterRef[], wanted: number | null, scopeTouched: boolean): SetupForm {
  const known = list.some((chapter) => chapter.id === current.chapterId)
  const chapterId = list.find((chapter) => chapter.id === wanted)?.id ?? (known ? current.chapterId : list[0]?.id) ?? null
  // "Whole chapter" is the default scope of a subject with chapters
  const scopeKind = current.scopeKind === 'subject' && !scopeTouched ? 'chapter' : current.scopeKind
  if (chapterId === current.chapterId) return { ...current, scopeKind }
  return { ...current, chapterId, scopeKind, topicIds: [], documentIds: [] }
}

function policyFrom(sources: string | null): SetupForm['sourcePolicy'] | null {
  // old links say expanded: All questions
  if (sources === 'materials') return 'materials'
  if (sources === 'all' || sources === 'expanded') return 'all'
  return null
}

function toggle(ids: number[], id: number, on: boolean): number[] {
  return on ? [...ids, id] : ids.filter((x) => x !== id)
}

const providerName = (provider: string) =>
  provider === 'openai'
    ? settingsStrings.keys.providers.openai
    : provider === 'anthropic'
      ? settingsStrings.keys.providers.anthropic
      : provider

/** A number input that lets the text be empty while typing; the clamped value is committed on every valid keystroke. */
function NumberField({
  id,
  value,
  min,
  max,
  step,
  onCommit,
  className = 'input num',
  label,
  disabled,
}: {
  id?: string
  value: number
  min: number
  max: number
  step?: number
  onCommit: (value: number) => void
  className?: string
  label?: string
  disabled?: boolean
}) {
  const [text, setText] = useState(String(value))
  const [prev, setPrev] = useState(value)
  if (prev !== value) {
    setPrev(value)
    setText(String(value))
  }
  return (
    <input
      className={className}
      id={id}
      aria-label={label}
      disabled={disabled}
      type="number"
      min={min}
      max={max}
      step={step}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        const parsed = Number(e.target.value)
        if (e.target.value !== '' && Number.isFinite(parsed)) {
          const clamped = Math.min(max, Math.max(min, parsed))
          setPrev(clamped)
          onCommit(clamped)
        }
      }}
      onBlur={() => setText(String(value))}
    />
  )
}

function Later() {
  return <span className="chip">{roundStrings.later}</span>
}

export function SetupPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [stored] = useState(readStored)
  const [form, setForm] = useState<SetupForm>(() => {
    const base = stored ?? defaultForm()
    // an untouched "Answer again" follows the feedback timing (exam: off)
    const initial = base.feedback === 'exam' ? { ...base, immediateRepeat: false } : base
    // ?sources= (coverage, trends, summaries, the guided dialog) preselects on arrival and on a new link
    const policy = policyFrom(params.get('sources'))
    return policy ? { ...initial, sourcePolicy: policy } : initial
  })
  const [subjects, setSubjects] = useState<SubjectRef[] | null>(null)
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  /** The voice list of one language; it is used only while it is the subject's language. */
  const [voices, setVoices] = useState<{ language: string; list: VoiceInfo[] } | null>(null)
  const [languages, setLanguages] = useState<ContentLanguage[] | null>(null)
  const languageRef = useRef('pl')
  /** The language whose audio defaults were applied last. */
  const appliedLanguage = useRef<string | null>(null)
  const [loaded, setData] = useState<SubjectData | null>(null)
  const [chapterData, setChapterData] = useState<{ subjectId: number; list: ChapterRef[] } | null>(null)
  /** ?chapter= preselects on arrival and on a new link, once the subject's chapters are loaded. */
  const [chapterLink, setChapterLink] = useState({ id: Number(params.get('chapter')) || null, n: 1 })
  /** The last chapter link adopted (each link is adopted once; later subject changes keep their own chapter). */
  const adoptedLink = useRef(0)
  /** The latest query, read when the subject list arrives. */
  const paramsRef = useRef(params)
  useEffect(() => {
    paramsRef.current = params
  })
  const [loadError, setLoadError] = useState(false)
  const [serverAvailable, setServerAvailable] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [estimate, setEstimate] = useState<RoundEstimate | null>(null)
  /** A round start refused by the spending ceiling: the server's message. */
  const [budgetMessage, setBudgetMessage] = useState<string | null>(null)
  const scopeRef = useRef<HTMLDivElement>(null)
  /** Fields the user changed; the settings defaults arriving later never overwrite them. */
  const touched = useRef(new Set<keyof SetupForm>())
  /** The learner changed "Answer again" (from then on it no longer follows the feedback timing). */
  const [repeatTouched, setRepeatTouched] = useState(false)

  /** Any change to what a server answer depended on clears that answer. */
  const update = useCallback((patch: Partial<SetupForm>) => {
    for (const key of Object.keys(patch) as (keyof SetupForm)[]) touched.current.add(key)
    if ('immediateRepeat' in patch) setRepeatTouched(true)
    const audioChanged = 'audio' in patch || 'voice' in patch || 'rate' in patch
    setForm((current) => {
      const next = { ...current, ...patch }
      if (audioChanged) writeMemory(languageRef.current, next)
      return next
    })
    setServerAvailable(null)
    setError(null)
  }, [])

  /** The feedback timing; an untouched "Answer again" follows it (checked only with Practice). */
  const setFeedback = useCallback(
    (feedback: FeedbackTiming) => {
      update({ feedback })
      if (!touched.current.has('immediateRepeat')) {
        setForm((current) => ({ ...current, immediateRepeat: feedback === 'practice' }))
      }
    },
    [update],
  )

  useEffect(() => {
    let cancelled = false
    Promise.all([apiGet<SubjectRef[]>('/subjects'), fetchPreferences(), listLanguages().catch(() => [])])
      .then(([list, preferences, languageList]) => {
        if (cancelled) return
        setSubjects(list)
        setPrefs(preferences)
        setLanguages(languageList)
        const latest = paramsRef.current
        const wanted = Number(latest.get('subject'))
        setForm((current) => {
          const chosen = list.find((s) => s.id === wanted) ?? list.find((s) => s.id === current.subjectId) ?? list[0]
          const next = { ...current, subjectId: chosen ? chosen.id : null }
          // ?type=adaptive (the coverage map's action) preselects, never over a touched choice
          if (latest.get('type') === 'adaptive' && !touched.current.has('roundType')) next.roundType = 'adaptive'
          if (!stored) {
            // the settings may carry the round defaults; without them the form keeps 10/30/120
            const defaults: Preferences & Partial<{ round_question_count: number; round_prep_seconds: number; round_answer_seconds: number }> = preferences
            const apply = <K extends keyof SetupForm>(key: K, value: SetupForm[K] | undefined) => {
              if (value !== undefined && !touched.current.has(key)) next[key] = value
            }
            apply('questionCount', defaults.round_question_count)
            apply('prepSeconds', defaults.round_prep_seconds)
            apply('answerSeconds', defaults.round_answer_seconds)
          }
          return next
        })
      })
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
    }
    // the subject and type query parameters only preselect on arrival
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const subjectId = form.subjectId
  const subjectLanguage = subjects?.find((subject) => subject.id === subjectId)?.language ?? 'pl'
  useEffect(() => {
    languageRef.current = subjectLanguage
  })
  const subjectsReady = subjects !== null
  const languageRow = languages?.find((row) => row.code === subjectLanguage)
  const contentLang = languageRow?.bcp47 ?? browserLang(subjectLanguage)

  // This language's voices only; an answer for an earlier subject language is ignored.
  useEffect(() => {
    if (!subjectsReady) return
    let cancelled = false
    fetchVoices(subjectLanguage)
      .catch(() => [] as VoiceInfo[])
      .then((list) => !cancelled && setVoices({ language: subjectLanguage, list }))
    return () => {
      cancelled = true
    }
  }, [subjectLanguage, subjectsReady])

  const languageVoices = voices?.language === subjectLanguage ? voices.list : null

  // The audio of the subject's language, once per language: its memory, the stored form (on arrival), its row, the settings.
  useEffect(() => {
    if (!prefs || !languages || !languageVoices) return
    if (appliedLanguage.current === subjectLanguage) return
    const arrival = appliedLanguage.current === null
    appliedLanguage.current = subjectLanguage
    if (arrival && ['audio', 'voice', 'rate'].some((key) => touched.current.has(key as keyof SetupForm))) return
    const ready = (audio: AudioChoice, voice: string) =>
      premium(audio) && languageVoices.some((v) => v.provider === audio && v.id === voice && v.installed) ? voice : ''
    const sameLanguage = (id: number | null) =>
      (subjects?.find((subject) => subject.id === id)?.language ?? 'pl') === subjectLanguage
    const remembered = readMemory()[subjectLanguage]
    let choice: { audio: AudioChoice; voice: string; rate: number }
    if (remembered) {
      choice = remembered
    } else if (arrival && stored && sameLanguage(stored.subjectId)) {
      choice = { audio: stored.audio, voice: stored.voice, rate: stored.rate }
    } else if (languageRow?.voice_provider) {
      choice = { audio: languageRow.voice_provider as AudioChoice, voice: languageRow.voice_name ?? '', rate: prefs.voice_rate }
    } else if (subjectLanguage === 'pl') {
      choice = { audio: prefs.voice_provider, voice: prefs.voice_name, rate: prefs.voice_rate }
    } else {
      const piper = languageVoices.find((v) => v.provider === 'piper' && v.installed)
      choice = piper
        ? { audio: 'piper', voice: piper.id, rate: prefs.voice_rate }
        : { audio: 'browser', voice: '', rate: prefs.voice_rate }
    }
    const voice = ready(choice.audio, choice.voice)
    setForm((current) => ({ ...current, audio: choice.audio, voice, rate: choice.rate }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs, languages, languageVoices, subjectLanguage])
  const data = loaded && loaded.subjectId === subjectId ? loaded : null
  useEffect(() => {
    if (subjectId === null) return
    let cancelled = false
    Promise.all([
      apiGet<TopicNode[]>(`/topics?subject_id=${subjectId}`),
      apiGet<DocumentRef[]>(`/subjects/${subjectId}/documents`),
      apiGet<QuestionFacts[]>(`/questions?subject_id=${subjectId}`),
    ])
      .then(([topics, documents, questions]) => {
        if (cancelled) return
        const rows = flatten(topics)
        setData({ subjectId, topics: rows, documents, questions })
        setForm((current) => ({
          ...current,
          topicIds: current.topicIds.filter((id) => rows.some((row) => row.topic.id === id)),
          documentIds: current.documentIds.filter((id) => documents.some((d) => d.id === id)),
        }))
      })
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
    }
  }, [subjectId])

  // The round's cost range follows what it depends on; a failed estimate shows nothing.
  const { questionCount, answerSeconds, followUps } = form
  useEffect(() => {
    if (subjectId === null) return
    let cancelled = false
    fetchRoundEstimate({ subjectId, questionCount, answerSeconds, followUps })
      .then((loaded) => {
        if (cancelled) return
        setEstimate(loaded)
        setBudgetMessage(null)
      })
      .catch(() => {
        if (cancelled) return
        // A stale refusal must not keep Start disabled; the server checks again on Start.
        setEstimate(null)
        setBudgetMessage(null)
      })
    return () => {
      cancelled = true
    }
  }, [subjectId, questionCount, answerSeconds, followUps])

  // chapters only for a subject with two or more; with one, the form and its body are as before
  const subjectRef = subjects?.find((subject) => subject.id === subjectId)
  const chaptered = (subjectRef?.chapter_count ?? 1) >= 2
  useEffect(() => {
    if (subjectId === null || !chaptered) return
    let cancelled = false
    apiGet<ChapterRef[]>(`/subjects/${subjectId}/chapters`)
      .then((list) => {
        if (cancelled) return
        setChapterData({ subjectId, list })
        const wanted = adoptedLink.current === chapterLink.n ? null : chapterLink.id
        adoptedLink.current = chapterLink.n
        setForm((current) => adoptChapter(current, list, wanted, touched.current.has('scopeKind')))
      })
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
    }
  }, [subjectId, chaptered, chapterLink])

  // a new link while the page is open (the guided dialog's "Start a round" on /practice) applies like an arrival
  const subjectQuery = params.get('subject')
  const chapterQuery = params.get('chapter')
  const sourcesQuery = params.get('sources')
  const queryKey = `${subjectQuery}|${chapterQuery}|${sourcesQuery}`
  const [seenQuery, setSeenQuery] = useState(queryKey)
  if (seenQuery !== queryKey) {
    setSeenQuery(queryKey)
    const policy = policyFrom(sourcesQuery)
    const wantedSubject = Number(subjectQuery)
    const known = subjects?.some((subject) => subject.id === wantedSubject) ?? false
    const wantedChapter = Number(chapterQuery) || null
    // the chapters are read again and the linked chapter adopted
    if (wantedChapter !== null) setChapterLink({ id: wantedChapter, n: chapterLink.n + 1 })
    setServerAvailable(null)
    setError(null)
    setForm((current) => {
      let next = policy ? { ...current, sourcePolicy: policy } : current
      if (known && wantedSubject !== current.subjectId)
        next = { ...next, subjectId: wantedSubject, topicIds: [], documentIds: [], chapterId: null }
      return next
    })
  }

  useEffect(() => {
    if (subjects) writeStored(form)
  }, [form, subjects])

  const chapters =
    chaptered && chapterData?.subjectId === subjectId && chapterData.list.length >= 2 ? chapterData.list : null
  const chapter = chapters ? (chapters.find((item) => item.id === form.chapterId) ?? chapters[0]) : null
  const chapterTopics = data ? data.topics.filter(({ topic }) => chapter === null || topic.chapter_id === chapter.id) : []
  const shownDocuments = data
    ? data.documents.filter((document) => chapter === null || document.chapter_id === chapter.id)
    : []
  /** The form as counted and sent: the chapter only while the subject has chapters, and only the shown topics and documents. */
  const effective: SetupForm = chapter
    ? {
        ...form,
        chapterId: chapter.id,
        chapterTopicIds: chapterTopics.map(({ topic }) => topic.id),
        topicIds: form.topicIds.filter((id) => chapterTopics.some(({ topic }) => topic.id === id)),
        documentIds: form.documentIds.filter((id) => shownDocuments.some((document) => document.id === id)),
      }
    : { ...form, chapterId: null, chapterTopicIds: [], scopeKind: form.scopeKind === 'chapter' ? 'subject' : form.scopeKind }
  const chapterName = chapter ? (chapter.name ?? ch.unnamed) : ''

  const computed = data ? availableCount(data.questions, effective) : null
  const available = effective.scopeKind === 'documents' ? serverAvailable : computed
  const notice = setupNotice({
    form: effective,
    subjectTotal: data ? data.questions.length : 1,
    available,
    error,
  })
  const blocked = notice.kind !== 'ok' && notice.kind !== 'error'
  const badTimers = invalidTimers(form)
  const conflict = setupConflict(form, repeatTouched)
  const overBudget = budgetMessage !== null || (estimate !== null && !estimate.fits)
  const canStart =
    !blocked && !conflict && !overBudget && badTimers.length === 0 && !busy && subjectId !== null && data !== null &&
    // Until the subject language's voices are in, the form may still hold another language's voice.
    languageVoices !== null

  const filters = [
    form.style === 'mixed' ? '' : t.styles[form.style],
    form.difficulty === 'mixed' ? '' : t.difficulties[form.difficulty],
  ]
    .filter(Boolean)
    .join(' · ')

  const scopeCount = form.scopeKind === 'topics' ? effective.topicIds.length : effective.documentIds.length
  const scopeText =
    effective.scopeKind === 'chapter'
      ? ch.notices.scopeChapter(chapterName)
      : effective.scopeKind === 'subject'
        ? t.notices.scopeWhole
        : form.scopeKind === 'topics'
          ? t.notices.scopeTopics(scopeCount)
          : t.notices.scopeDocuments(scopeCount)

  const voiceChoices = useMemo(
    () => (languageVoices ?? []).filter((voice) => voice.provider === form.audio && voice.installed),
    [languageVoices, form.audio],
  )
  /** An engine is ready when the language has an installed voice of it; unknown until the list is loaded. */
  const engineReady = (audio: AudioChoice) =>
    languageVoices === null || languageVoices.some((voice) => voice.provider === audio && voice.installed)
  const noPremium =
    languageVoices !== null &&
    (!(['piper', 'azure_speech', 'openai'] as AudioChoice[]).some(engineReady) ||
      (premium(form.audio) && !engineReady(form.audio)))
  const languageName = languageRow?.name_en ?? subjectLanguage

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!canStart) return
    setBusy(true)
    setError(null)
    try {
      const created = await createRound(toRoundCreate(effective))
      prefetchRound(created) // the first question's voice is made while the microphone is checked
      navigate(`/practice/${created.id}/microphone`)
    } catch (e) {
      const detail = e instanceof ApiError ? (e.detail as { available?: unknown; estimate?: unknown } | undefined) : undefined
      if (e instanceof ApiError && e.status === 409 && typeof detail?.available === 'number' && effective.scopeKind === 'documents') {
        setServerAvailable(detail.available)
      } else if (e instanceof ApiError && e.status === 409 && detail?.estimate) {
        setBudgetMessage(e.message)
      } else {
        setError(e instanceof Error ? e.message : t.loadError)
      }
      setBusy(false)
    }
  }

  const preview = async () => {
    setPreviewError(false)
    try {
      if (form.audio === 'browser') {
        if (!speakSample(subjectLanguage, contentLang, form.rate)) setPreviewError(true)
      } else if (premium(form.audio)) {
        const { audio_url } = await previewVoice(form.audio, form.voice, form.rate, subjectLanguage)
        if (audio_url) await new Audio(audio_url).play()
      }
    } catch {
      setPreviewError(true)
    }
  }

  const topicCount =
    effective.scopeKind === 'subject'
      ? (data?.topics.length ?? 0)
      : effective.scopeKind === 'chapter'
        ? chapterTopics.length
        : effective.topicIds.length
  const summaryFrom =
    form.scopeKind === 'documents'
      ? t.sumFrom(form.questionCount, effective.documentIds.length, 'documents')
      : t.sumFrom(form.questionCount, topicCount, 'topics')
  const minutes = estimateMinutes(form.questionCount, form.prepSeconds, form.answerSeconds)
  const subjectOptions = subjects ?? []

  if (loadError) {
    return (
      <div className="notice error" role="alert">
        <svg {...svgProps}>
          <circle cx="10" cy="10" r="7.5" />
          <path d="M10 6v4.5" />
          <path d="M10 13.5v.01" />
        </svg>
        <div>{t.loadError}</div>
      </div>
    )
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t.title}</h1>
          <p>{t.intro}</p>
        </div>
      </div>

      <div className="cols-main">
        <form className="setup" id="round-setup" onSubmit={(e) => void submit(e)}>
          <fieldset>
            <legend>
              <h2>{t.practise}</h2>
            </legend>
            <div className="field">
              <label className="label" htmlFor="subj">
                {t.subject}
              </label>
              <select
                className="select content"
                id="subj"
                value={subjectId ?? ''}
                onChange={(e) => update({ subjectId: Number(e.target.value), topicIds: [], documentIds: [], chapterId: null })}
              >
                {subjectOptions.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {plNbsp(subject.name)}
                  </option>
                ))}
              </select>
            </div>

            {chapters && chapter && (
              <div className="field">
                <label className="label" htmlFor="chap">
                  {ch.chapter}
                </label>
                <select
                  className="select content"
                  id="chap"
                  value={chapter.id}
                  onChange={(e) => update({ chapterId: Number(e.target.value), topicIds: [], documentIds: [] })}
                >
                  {chapters.map((item) =>
                    item.name === null ? (
                      <option key={item.id} value={item.id}>
                        {ch.unnamed}
                      </option>
                    ) : (
                      <option key={item.id} value={item.id}>
                        {plNbsp(item.name)}
                      </option>
                    ),
                  )}
                </select>
              </div>
            )}

            <div className="subgroup" role="radiogroup" aria-labelledby="scope-l" ref={scopeRef}>
              <span className="label" id="scope-l">
                {t.scope}
              </span>
              <div className="choice-group">
                {(chapter
                  ? ([
                      ['chapter', ch.scopeChapter],
                      ['subject', ch.scopeWholeSubject],
                      ['topics', t.scopeTopics],
                      ['documents', t.scopeDocuments],
                    ] as [ScopeKind, string][])
                  : ([
                      ['subject', t.scopeWhole],
                      ['topics', t.scopeTopics],
                      ['documents', t.scopeDocuments],
                    ] as [ScopeKind, string][])
                ).map(([kind, label]) => (
                  <label className="choice" key={kind}>
                    <input
                      type="radio"
                      name="scope"
                      checked={effective.scopeKind === kind}
                      onChange={() => update({ scopeKind: kind })}
                    />{' '}
                    {label}
                  </label>
                ))}
              </div>
              {form.scopeKind === 'topics' &&
                (data && chapterTopics.length === 0 ? (
                  <p className="small muted">{t.noTopics}</p>
                ) : (
                  <ul className="rows topics" aria-label={t.topicsList}>
                    {chapterTopics.map(({ topic, child }) => (
                      <li key={topic.id} className={child ? 'child' : undefined}>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={effective.topicIds.includes(topic.id)}
                            onChange={(e) => update({ topicIds: toggle(effective.topicIds, topic.id, e.target.checked) })}
                          />{' '}
                          <span className="content" lang={contentLang}>
                            {subjectLanguage === 'pl' ? plNbsp(topic.title) : topic.title}
                          </span>
                        </label>
                        <span className="end">
                          <span className="num">{t.questionCountOf(topic.question_count)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                ))}
              {form.scopeKind === 'documents' &&
                (data && shownDocuments.length === 0 ? (
                  <p className="small muted">{t.noDocuments}</p>
                ) : (
                  <ul className="rows topics" aria-label={t.documentsList}>
                    {shownDocuments.map((document) => (
                      <li key={document.id}>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={effective.documentIds.includes(document.id)}
                            onChange={(e) =>
                              update({ documentIds: toggle(effective.documentIds, document.id, e.target.checked) })
                            }
                          />{' '}
                          <span className="content">
                            {plNbsp(document.title)}
                          </span>
                        </label>
                        {document.latest_version.passage_count !== null && (
                          <span className="end">
                            <span className="num">{t.passageCount(document.latest_version.passage_count)}</span>
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ))}
            </div>

            <div className="subgroup" role="radiogroup" aria-labelledby="src-l">
              <span className="label" id="src-l">
                {sv.practiceLabel}
              </span>
              <div className="options">
                <label className="choice opt">
                  <input
                    type="radio"
                    name="pol"
                    checked={form.sourcePolicy === 'materials'}
                    onChange={() => update({ sourcePolicy: 'materials' })}
                  />{' '}
                  <span>
                    <strong>{sv.round.materials}</strong>
                    {sv.materialsText}
                  </span>
                </label>
                <label className="choice opt">
                  <input
                    type="radio"
                    name="pol"
                    checked={form.sourcePolicy !== 'materials'}
                    onChange={() => update({ sourcePolicy: 'all' })}
                  />{' '}
                  <span>
                    <strong>{sv.round.all}</strong>
                    {sv.allText}
                  </span>
                </label>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.roundType}</h2>
            </legend>
            <div className="options" role="radiogroup" aria-label={t.roundType}>
              <label className="choice opt">
                <input
                  type="radio"
                  name="type"
                  checked={form.roundType === 'standard'}
                  onChange={() => update({ roundType: 'standard' })}
                />{' '}
                <span>
                  <strong>{t.typeStandard}</strong>
                  {t.typeStandardText}
                </span>
              </label>
              <label className="choice opt">
                <input
                  type="radio"
                  name="type"
                  checked={form.roundType === 'adaptive'}
                  onChange={() => update({ roundType: 'adaptive' })}
                />{' '}
                <span>
                  <strong>{t.typeAdaptive}</strong>
                  {t.typeAdaptiveText}
                </span>
              </label>
              <label className="choice opt off">
                <input type="radio" name="type" disabled />{' '}
                <span>
                  <strong>{t.typeBenchmark}</strong>
                  {t.typeBenchmarkText} <Later />
                </span>
              </label>
            </div>
            {form.roundType === 'adaptive' && <p className="hint">{a.hint}</p>}
            <div className="subgroup" role="radiogroup" aria-labelledby="fb-l">
              <span className="label" id="fb-l">
                {t.feedback}
              </span>
              <div className="options">
                <label className="choice opt">
                  <input
                    type="radio"
                    name="fb"
                    checked={form.feedback === 'practice'}
                    onChange={() => setFeedback('practice')}
                  />{' '}
                  <span>
                    <strong>{t.feedbackPractice}</strong>
                    {t.feedbackPracticeText}
                  </span>
                </label>
                <label className="choice opt">
                  <input
                    type="radio"
                    name="fb"
                    checked={form.feedback === 'exam'}
                    onChange={() => setFeedback('exam')}
                  />{' '}
                  <span>
                    <strong>{t.feedbackExam}</strong>
                    {t.feedbackExamText}
                  </span>
                </label>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.questions}</h2>
            </legend>
            <div className="fields num-first">
              <div className="field">
                <label className="label" htmlFor="n">
                  {t.count}
                </label>
                <NumberField id="n" value={form.questionCount} min={1} max={30} onCommit={(v) => update({ questionCount: v })} />
                {available !== null && (
                  <span className="hint">
                    {filters ? t.matchSettings(available) : t.availableInScope(available)}
                  </span>
                )}
              </div>
              <div className="field">
                <label className="label" htmlFor="st">
                  {t.style}
                </label>
                <select
                  className="select"
                  id="st"
                  value={form.style}
                  onChange={(e) => update({ style: e.target.value as StyleChoice })}
                >
                  {(['mixed', 'short', 'open'] as StyleChoice[]).map((value) => (
                    <option key={value} value={value}>
                      {t.styles[value]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label className="label" htmlFor="df">
                  {t.difficulty}
                </label>
                <select
                  className="select"
                  id="df"
                  value={form.difficulty}
                  onChange={(e) => update({ difficulty: e.target.value as DifficultyChoice })}
                >
                  {(['mixed', 'recall', 'apply', 'analysis'] as DifficultyChoice[]).map((value) => (
                    <option key={value} value={value}>
                      {t.difficulties[value]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.time}</h2>
            </legend>
            <div className="fields inline">
              <div className="field">
                <label className="label" htmlFor="tp">
                  {t.prep}
                </label>
                <NumberField id="tp" value={form.prepSeconds} min={0} max={600} onCommit={(v) => update({ prepSeconds: v })} />
                <span className="hint">{t.prepHint}</span>
              </div>
              <div className="field">
                <label className="label" htmlFor="ts">
                  {t.answer}
                </label>
                <NumberField id="ts" value={form.answerSeconds} min={5} max={600} onCommit={(v) => update({ answerSeconds: v })} />
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.extra}</h2>
            </legend>
            <div className="options">
              <div className="choice opt">
                <input type="checkbox" id="fu" checked={form.followUps} onChange={(e) => update({ followUps: e.target.checked })} />{' '}
                <span>
                  <label htmlFor="fu">
                    <strong>{t.extraFollowUp}</strong>
                    {t.extraFollowUpText}
                  </label>{' '}
                  · {t.followUpPrepare}{' '}
                  <NumberField
                    className="input tiny"
                    label={t.followUpPrepLabel}
                    value={form.followUpPrepSeconds}
                    min={PREP_RANGE[0]}
                    max={PREP_RANGE[1]}
                    disabled={!form.followUps}
                    onCommit={(v) => update({ followUpPrepSeconds: v })}
                  />{' '}
                  {t.secondsUnit}, {t.followUpAnswer}{' '}
                  <NumberField
                    className="input tiny"
                    label={t.followUpAnswerLabel}
                    value={form.followUpAnswerSeconds}
                    min={ANSWER_RANGE[0]}
                    max={ANSWER_RANGE[1]}
                    disabled={!form.followUps}
                    onCommit={(v) => update({ followUpAnswerSeconds: v })}
                  />{' '}
                  {t.secondsUnit}
                </span>
              </div>
              <label className="choice opt">
                <input
                  type="checkbox"
                  checked={form.immediateRepeat}
                  aria-describedby={conflict ? 'conflict' : undefined}
                  onChange={(e) => update({ immediateRepeat: e.target.checked })}
                />{' '}
                <span>
                  <strong>{t.extraAgain}</strong>
                  {t.extraAgainText}
                  {conflict && (
                    <>
                      {' '}
                      <span className="chip warn">{r.needsPractice}</span>
                    </>
                  )}
                </span>
              </label>
              <label className="choice opt">
                <input type="checkbox" checked={form.retryRound} onChange={(e) => update({ retryRound: e.target.checked })} />{' '}
                <span>
                  <strong>{t.extraRetry}</strong>
                  {t.extraRetryText}
                </span>
              </label>
              <div className="choice opt">
                <input type="checkbox" id="ext" checked={form.extension} onChange={(e) => update({ extension: e.target.checked })} />{' '}
                <span>
                  <label htmlFor="ext">
                    <strong>{a.extension}</strong>
                    {a.extensionAt}
                  </label>{' '}
                  <NumberField
                    className="input tiny"
                    label={a.extensionCount}
                    value={form.extensionCount}
                    min={EXTENSION_RANGE[0]}
                    max={EXTENSION_RANGE[1]}
                    disabled={!form.extension}
                    onCommit={(v) => update({ extensionCount: v })}
                  />
                  {a.extensionText}
                </span>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.audio}</h2>
            </legend>
            <div className="fields">
              <div className="field">
                <label className="label" htmlFor="au">
                  {t.readAloud}
                </label>
                <select
                  className="select"
                  id="au"
                  value={form.audio}
                  onChange={(e) => update({ audio: e.target.value as AudioChoice, voice: '' })}
                >
                  <option value="off">{t.audioOff}</option>
                  <option value="piper" disabled={!engineReady('piper') && form.audio !== 'piper'}>
                    {t.audioPiper}
                  </option>
                  <option value="azure_speech" disabled={!engineReady('azure_speech') && form.audio !== 'azure_speech'}>
                    {t.audioAzure}
                  </option>
                  <option value="openai" disabled={!engineReady('openai') && form.audio !== 'openai'}>
                    {settingsStrings.openaiVoice.roundOption}
                  </option>
                  <option value="browser">{t.audioBrowser}</option>
                </select>
                {noPremium && (
                  <p className="small muted">
                    {settingsStrings.languages.noPremium(languageName).replace(settingsStrings.languages.settingsLink + '.', '')}
                    <Link to="/settings#languages">{settingsStrings.languages.settingsLink}</Link>.
                  </p>
                )}
              </div>
              {premium(form.audio) && (
                <div className="field">
                  <label className="label" htmlFor="vo">
                    {t.voice}
                  </label>
                  <select
                    className="select"
                    id="vo"
                    value={voiceChoices.some((voice) => voice.id === form.voice) ? form.voice : ''}
                    onChange={(e) => update({ voice: e.target.value })}
                  >
                    <option value="">{t.voiceDefault}</option>
                    {voiceChoices.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {form.audio !== 'off' && (
                <div className="field">
                  <label className="label" htmlFor="ra">
                    {t.rate}
                  </label>
                  <NumberField id="ra" value={form.rate} min={0.5} max={1.5}
                    step={0.1} onCommit={(v) => update({ rate: v })} />
                </div>
              )}
            </div>
            {form.audio !== 'off' && (
              <div className="actions subgroup">
                <button type="button" className="btn small" onClick={() => void preview()}>
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M6.5 4.5v11l9-5.5z" />
                  </svg>
                  {t.preview}
                </button>
                {previewError && <span className="small muted">{t.previewFailed}</span>}
              </div>
            )}
          </fieldset>

          <fieldset>
            <legend>
              <h2>{t.assessment}</h2>
            </legend>
            {prefs && (
              <p className="small muted">
                {t.assessmentLine(
                  `${providerName(prefs.generation_provider)} ${prefs.generation_model}`,
                  `${providerName(prefs.assessment_provider)} ${prefs.assessment_model}`,
                  `${providerName('openai')} ${prefs.transcription_model}`,
                )}{' '}
                <Link to="/settings">{t.changeInSettings}</Link>
              </p>
            )}
            <div className="options subgroup">
              <label className="choice opt off">
                <input type="checkbox" disabled />{' '}
                <span>
                  {t.twoModels} <Later />
                </span>
              </label>
            </div>
          </fieldset>
        </form>

        <aside className="box tint setup-summary" aria-labelledby="sum-h">
          <h2 id="sum-h">{t.summary}</h2>
          <dl className="facts stacked">
            <dt>{t.sumQuestions}</dt>
            <dd>
              {summaryFrom}
              {available !== null && <> <span className="muted">{t.sumAvailable(available)}</span></>}
            </dd>
            <dt>{t.sumSetup}</dt>
            <dd>
              {roundTypeLabel(form.roundType)} ·{' '}
              {conflict ? <strong>{t.feedbackExamShort}</strong> : form.feedback === 'exam' ? t.feedbackExamShort : t.feedbackPracticeShort} ·{' '}
              {roundPolicyLabel(form.sourcePolicy === 'materials' ? 'materials' : 'all')}
            </dd>
            <dt>{t.sumTime}</dt>
            <dd>{t.sumMinutes(minutes)}</dd>
            {estimate && (
              <>
                <dt>{c.estimated}</dt>
                <dd>
                  <strong className="num">{usdRange(estimate.low_usd, estimate.high_usd)}</strong>
                  <br />
                  <small className="muted">{estimateLine(estimate)}</small>
                </dd>
              </>
            )}
          </dl>

          {conflict && (
            <div className="notice warn" role="status" id="conflict">
              <svg {...svgProps}>
                <path d="M10 3.5l7 12.5H3z" />
                <path d="M10 8.5v3.5" />
                <path d="M10 14.2v.1" />
              </svg>
              <div>
                <strong>{r.conflictTitle}</strong> {r.conflictBody}
                <div className="actions">
                  <button type="button" className="btn small" onClick={() => setFeedback('practice')}>
                    {r.switchPractice}
                  </button>
                  <button type="button" className="btn small" onClick={() => update({ immediateRepeat: false })}>
                    {r.turnOff}
                  </button>
                </div>
              </div>
            </div>
          )}
          {overBudget && (
            <div className="notice error" role="status">
              <svg {...svgProps}>
                <circle cx="10" cy="10" r="7" />
                <path d="M10 6.5v4" />
                <path d="M10 13.3v.1" />
              </svg>
              <div>
                <strong>{c.budgetTitle}</strong>{' '}
                {budgetMessage ??
                  (estimate && (
                    <>
                      {c.soFar(monthName(estimate.month.month).split(' ')[0])}{' '}
                      <span className="num">{usd(estimate.month.spent_usd + estimate.month.reserved_usd)}</span> {c.of}{' '}
                      <span className="num">{usd(estimate.month.ceiling_usd)}</span>. {c.needs}{' '}
                      <span className="num">{usdRange(estimate.low_usd, estimate.high_usd)}</span>
                      {c.cannotStart}
                    </>
                  ))}
                <div className="actions">
                  <Link className="btn small" to="/settings#spending">
                    {c.changeCeiling}
                  </Link>
                  <Link className="btn small" to="/settings/usage">
                    {c.seeUsage}
                  </Link>
                </div>
              </div>
            </div>
          )}
          {notice.kind === 'ok' && !conflict && !overBudget && (
            <div className="notice ok" role="status">
              <svg {...svgProps}>
                <path d="M4.5 10.5l3.5 3.5 7.5-8" />
              </svg>
              <span>{t.notices.ok}</span>
            </div>
          )}
          {notice.kind === 'no-questions' && (
            <div className="notice warn" role="status">
              <svg {...svgProps}>
                <path d="M10 3.5l7 12.5H3z" />
                <path d="M10 8.5v3.5" />
                <path d="M10 14.2v.1" />
              </svg>
              <div>
                <strong>{t.notices.noQuestions}</strong> {t.notices.noQuestionsHelp}
                <div className="actions">
                  <Link className="btn small" to={subjectId === null ? '/library' : `/library/${subjectId}`}>
                    {t.notices.toLibrary}
                  </Link>
                </div>
              </div>
            </div>
          )}
          {(notice.kind === 'need-topics' || notice.kind === 'need-documents') && (
            <div className="notice warn" role="status">
              <svg {...svgProps}>
                <path d="M10 3.5l7 12.5H3z" />
                <path d="M10 8.5v3.5" />
                <path d="M10 14.2v.1" />
              </svg>
              <div>{notice.kind === 'need-topics' ? t.notices.needTopics : t.notices.needDocuments}</div>
            </div>
          )}
          {notice.kind === 'only' && (
            <div className="notice warn" role="status" id="short">
              <svg {...svgProps}>
                <path d="M10 3.5l7 12.5H3z" />
                <path d="M10 8.5v3.5" />
                <path d="M10 14.2v.1" />
              </svg>
              <div>
                <strong>{t.notices.only(notice.available)}</strong>{' '}
                {t.notices.onlyBody(filters, scopeText, notice.available, form.questionCount)}
                <div className="actions">
                  {notice.available > 0 && (
                    <button type="button" className="btn small" onClick={() => update({ questionCount: notice.available })}>
                      {t.notices.ask(notice.available)}
                    </button>
                  )}
                  {form.style !== 'mixed' && (
                    <button type="button" className="btn small" onClick={() => update({ style: 'mixed' })}>
                      {t.notices.anyStyle}
                    </button>
                  )}
                  {form.difficulty !== 'mixed' && (
                    <button type="button" className="btn small" onClick={() => update({ difficulty: 'mixed' })}>
                      {t.notices.anyDifficulty}
                    </button>
                  )}
                  {form.scopeKind === 'topics' && (
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => scopeRef.current?.querySelector<HTMLInputElement>('ul input:not(:checked)')?.focus()}
                    >
                      {t.notices.moreTopics}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          {notice.kind === 'error' && (
            <div className="notice error" role="alert">
              <svg {...svgProps}>
                <circle cx="10" cy="10" r="7.5" />
                <path d="M10 6v4.5" />
                <path d="M10 13.5v.01" />
              </svg>
              <div>
                {notice.message}
                <div className="actions">
                  <Link className="btn small" to="/settings">
                    {t.notices.settings}
                  </Link>
                </div>
              </div>
            </div>
          )}

          {badTimers.length > 0 && <Notice kind="warn">{t.invalidTimers(badTimers.map((key) => t.timerNames[key]))}</Notice>}

          <button type="submit" form="round-setup" className="btn primary big" disabled={!canStart}>
            {busy ? t.starting : t.start}
          </button>
        </aside>
      </div>
    </>
  )
}

/** "Transcription about 25 min, assessment, follow-ups; voice free (Piper). This month: …" */
function estimateLine(estimate: RoundEstimate): string {
  const parts = [c.transcription(Math.round(estimate.transcription_minutes)), c.assessment]
  if (estimate.parts.some((part) => part.operation === 'follow_up.generate')) parts.push(c.followUps)
  const voicePart = estimate.parts.find((part) => part.operation === 'voice.synthesize')
  const voiceName = estimate.voice_provider === 'piper' ? 'Piper' : providerLabel(estimate.voice_provider)
  const voice = voicePart ? c.voicePaid(usd(voicePart.usd), voiceName) : c.voiceFree(voiceName)
  const month = estimate.month
  const spent = usd(month.spent_usd + month.reserved_usd)
  const monthText = month.ceiling_usd > 0 ? c.monthOf(spent, usd(month.ceiling_usd)) : c.monthSpent(spent)
  const line = `${parts.join(', ')}; ${voice}. ${monthText}`
  return estimate.basis === 'measured' ? `${line} ${c.measured}` : line
}
