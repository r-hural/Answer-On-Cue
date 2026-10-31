import type { ContentLanguage, Job, Topic, Version } from '../library/api'
import { EXTRACT_KIND, GENERATE_KIND } from '../library/api'
import { isLive } from '../library/useJobs'
import type { QuestionAudio } from '../rounds/api'
import type { Preferences } from '../settings/api'

export type Step =
  | 'upload'
  | 'extract-start'
  | 'finding'
  | 'extract-failed'
  | 'topics'
  | 'generating'
  | 'generate-failed'
  | 'ready'
  | 'empty'

export const MAX_SUBJECT_NAME = 200
export const MAX_PER_TOPIC = 10
export const MAX_QUESTIONS = 30

/** The requested question count, or null unless it is a whole number from 1 to 30. */
export function questionCount(text: string): number | null {
  const count = Number(text)
  return text.trim() && Number.isInteger(count) && count >= 1 && count <= MAX_QUESTIONS ? count : null
}

/** Subject name for a quick-practice file: the stem, with `_` and `-` as spaces. */
export function subjectNameFromFile(filename: string): string {
  const dot = filename.lastIndexOf('.')
  const stem = dot > 0 ? filename.slice(0, dot) : filename
  const name = stem.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  return (name || 'Quick practice').slice(0, MAX_SUBJECT_NAME).trim()
}

/** Name candidates when the subject name is taken: the name, then "name (2)" … "name (9)". */
export function nameCandidates(name: string): string[] {
  const base = name.slice(0, MAX_SUBJECT_NAME - 4).trim()
  return [name, ...Array.from({ length: 8 }, (_, n) => `${base} (${n + 2})`)]
}

/** Questions per topic so that about `total` come out of `topics` ticked topics. */
export function perTopic(total: number, topics: number): number {
  if (topics < 1) return 1
  return Math.max(1, Math.min(MAX_PER_TOPIC, Math.ceil(total / topics)))
}

/** What the backend answers when a round asks for a premium voice the language does not have (rounds/service.py). */
export const NEED_PREMIUM_VOICE = 'Choose a premium voice in Settings.'

/**
 * The voice engine that reads a subject's questions: the language's own row, else for Polish the global
 * engine, else null (the language's default: its local voice if installed, else the browser voice).
 */
export function quickVoice(preferences: Preferences, code: string, row: ContentLanguage | undefined): string | null {
  if (row?.voice_provider) return row.voice_provider
  return code === 'pl' ? preferences.voice_provider : null
}

/** The server picks the premium voice of the subject's language; the browser voice is the one it cannot pick. */
export function quickAudio(provider: string | null): QuestionAudio {
  return provider === 'browser' ? { mode: 'browser' } : { mode: 'premium' }
}

export function audioFromPreferences(preferences: Preferences): QuestionAudio {
  return quickAudio(preferences.voice_provider)
}

const newest = (jobs: Job[], kind: string): Job | null =>
  jobs.filter((job) => job.kind === kind).sort((a, b) => b.id - a.id)[0] ?? null

const stopped = (job: Job) => job.status === 'failed' || (!isLive(job) && job.resumable)

export interface StepInput {
  version: Version | null
  jobs: Job[]
  topics: Topic[]
  /** Null while unknown: the newest generation ended after the page data was read. */
  activeQuestions: number | null
  /** The user went back from the ready or empty step to the topics. */
  showTopics: boolean
}

/**
 * Whether the page data was read after the newest generation ended, so its question count holds.
 * `jobs` are the tasks seen by the poller, `loadedJobs` the ones read with the page data.
 */
export function countKnown(jobs: Job[], loadedJobs: Job[]): boolean {
  const generate = newest(jobs, GENERATE_KIND)
  if (!generate || isLive(generate)) return true
  return loadedJobs.some((job) => job.id === generate.id && !isLive(job))
}

/** The step the server state is in; leaving and reloading the page lands on the same step. */
export function deriveStep({ version, jobs, topics, activeQuestions, showTopics }: StepInput): Step {
  if (!version) return 'upload'
  if (version.status === 'failed') return 'extract-failed'
  const extract = newest(jobs, EXTRACT_KIND)
  if (extract && isLive(extract)) return 'finding'
  if (extract && stopped(extract)) return 'extract-failed'
  if (!extract && topics.length === 0) return 'extract-start'
  if (showTopics) return 'topics'
  const generate = newest(jobs, GENERATE_KIND)
  const current = generate && (!extract || generate.id > extract.id) ? generate : null
  if (current && isLive(current)) return 'generating'
  if (current && stopped(current)) return 'generate-failed'
  if (activeQuestions === null) return current ? 'generating' : 'topics'
  if (activeQuestions > 0) return 'ready'
  if (current) return 'empty'
  return 'topics'
}

/** Index of the highlighted stage (0 = Upload … 3 = Start). */
export function stageOf(step: Step): number {
  switch (step) {
    case 'upload':
      return 0
    case 'extract-start':
    case 'finding':
    case 'extract-failed':
    case 'topics':
      return 1
    case 'generating':
    case 'generate-failed':
      return 2
    default:
      return 3
  }
}

/** "30 s", "2 minutes". */
export function spoken(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) {
    const minutes = seconds / 60
    return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
  }
  return `${seconds} s`
}

export interface Remembered {
  count: number
  topicIds: number[]
}

const key = (subjectId: number) => `aoc.quick.${subjectId}`

/** The requested question count (and topics) survive a reload of the generating step. */
export function remember(subjectId: number, value: Remembered): void {
  try {
    sessionStorage.setItem(key(subjectId), JSON.stringify(value))
  } catch {
    // storage may be blocked; the step then says "Preparing questions…"
  }
}

export function recall(subjectId: number): Remembered | null {
  try {
    const raw = sessionStorage.getItem(key(subjectId))
    if (!raw) return null
    const value = JSON.parse(raw) as Remembered
    return Number.isInteger(value.count) && Array.isArray(value.topicIds) ? value : null
  } catch {
    return null
  }
}
