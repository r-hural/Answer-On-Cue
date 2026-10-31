import { describe, expect, it } from 'vitest'
import { LANGUAGES } from '../library/languages.mocks'
import { job, topic, version } from '../library/library.mocks'
import { preferences } from './quick.mocks'
import {
  audioFromPreferences,
  countKnown,
  deriveStep,
  nameCandidates,
  perTopic,
  quickAudio,
  quickVoice,
  questionCount,
  recall,
  remember,
  spoken,
  subjectNameFromFile,
} from './quickState'

const extract = (patch = {}) => job({ id: 7, kind: 'topics.extract', ...patch })
const generate = (patch = {}) => job({ id: 8, kind: 'questions.generate', ...patch })
const base = {
  version: version(),
  jobs: [],
  topics: [],
  activeQuestions: 0,
  showTopics: false,
}

describe('subjectNameFromFile', () => {
  it('drops the extension and turns _ and - into spaces', () => {
    expect(subjectNameFromFile('Biologia_komorki_rozdzial_3.pdf')).toBe('Biologia komorki rozdzial 3')
    expect(subjectNameFromFile('notatki-z-historii.docx')).toBe('notatki z historii')
  })

  it('keeps at most 200 characters and never returns an empty name', () => {
    expect(subjectNameFromFile(`${'a'.repeat(300)}.txt`)).toHaveLength(200)
    expect(subjectNameFromFile('___.txt')).toBe('Quick practice')
  })

  it('offers " (2)" … " (9)" when the name is taken', () => {
    const names = nameCandidates('Ateny')
    expect(names[0]).toBe('Ateny')
    expect(names[1]).toBe('Ateny (2)')
    expect(names[8]).toBe('Ateny (9)')
    expect(names).toHaveLength(9)
    expect(nameCandidates('a'.repeat(200)).every((name) => name.length <= 200)).toBe(true)
  })
})

describe('perTopic', () => {
  it('spreads the questions over the ticked topics, at most 10 each', () => {
    expect(perTopic(8, 3)).toBe(3)
    expect(perTopic(30, 1)).toBe(10)
    expect(perTopic(1, 5)).toBe(1)
    expect(perTopic(10, 0)).toBe(1)
  })
})

describe('deriveStep', () => {
  it('starts with the upload when there is no document', () => {
    expect(deriveStep({ ...base, version: null })).toBe('upload')
  })

  it('reports a failed extraction of the file', () => {
    expect(deriveStep({ ...base, version: version({ status: 'failed' }) })).toBe('extract-failed')
  })

  it('starts the extraction when nothing ran yet, and follows a live one', () => {
    expect(deriveStep(base)).toBe('extract-start')
    expect(deriveStep({ ...base, jobs: [extract({ status: 'running' })] })).toBe('finding')
    expect(deriveStep({ ...base, jobs: [extract({ status: 'queued' })] })).toBe('finding')
  })

  it('treats a resumable or failed extraction job as stopped', () => {
    expect(deriveStep({ ...base, jobs: [extract({ status: 'running', resumable: true })] })).toBe('extract-failed')
    expect(deriveStep({ ...base, jobs: [extract({ status: 'failed' })] })).toBe('extract-failed')
  })

  it('shows the topics once they exist and no questions were requested', () => {
    expect(deriveStep({ ...base, topics: [topic()], jobs: [extract({ status: 'done' })] })).toBe('topics')
    expect(deriveStep({ ...base, topics: [topic()] })).toBe('topics')
  })

  it('follows the question generation', () => {
    const topics = [topic()]
    const done = extract({ status: 'done' })
    expect(deriveStep({ ...base, topics, jobs: [done, generate({ status: 'running' })] })).toBe('generating')
    expect(deriveStep({ ...base, topics, jobs: [done, generate({ status: 'failed' })] })).toBe('generate-failed')
    expect(
      deriveStep({ ...base, topics, jobs: [done, generate({ status: 'running', resumable: true })] }),
    ).toBe('generate-failed')
  })

  it('is ready with active questions and empty when a finished job made none', () => {
    const topics = [topic()]
    const jobs = [extract({ status: 'done' }), generate({ status: 'done' })]
    expect(deriveStep({ ...base, topics, jobs, activeQuestions: 6 })).toBe('ready')
    expect(deriveStep({ ...base, topics, jobs, activeQuestions: 0 })).toBe('empty')
  })

  it('waits on the generating step until the count is read after the job ended', () => {
    const topics = [topic()]
    const jobs = [extract({ status: 'done' }), generate({ status: 'done' })]
    expect(deriveStep({ ...base, topics, jobs, activeQuestions: null })).toBe('generating')
    const running = generate({ status: 'running' })
    expect(countKnown([generate({ status: 'done' })], [running])).toBe(false)
    expect(countKnown([generate({ status: 'done' })], [])).toBe(false)
    expect(countKnown([generate({ status: 'done' })], [generate({ status: 'done' })])).toBe(true)
    expect(countKnown([running], [running])).toBe(true)
    expect(countKnown([extract({ status: 'done' })], [])).toBe(true)
  })

  it('goes back to the topics on request, and ignores a generation older than the extraction', () => {
    const topics = [topic()]
    const jobs = [extract({ id: 7, status: 'done' }), generate({ id: 8, status: 'done' })]
    expect(deriveStep({ ...base, topics, jobs, activeQuestions: 6, showTopics: true })).toBe('topics')
    const newer = [extract({ id: 9, status: 'done' }), generate({ id: 8, status: 'done' })]
    expect(deriveStep({ ...base, topics, jobs: newer })).toBe('topics')
  })
})

describe('questionCount', () => {
  it('accepts whole numbers from 1 to 30 only', () => {
    expect(questionCount('1')).toBe(1)
    expect(questionCount('30')).toBe(30)
    expect(questionCount('')).toBeNull()
    expect(questionCount('0')).toBeNull()
    expect(questionCount('31')).toBeNull()
    expect(questionCount('2.5')).toBeNull()
  })
})

describe('audioFromPreferences', () => {
  it('maps the voice engine to the round audio', () => {
    expect(audioFromPreferences(preferences)).toEqual({ mode: 'premium' })
    expect(audioFromPreferences({ ...preferences, voice_provider: 'azure_speech', voice_name: 'pl-PL-ZofiaNeural', voice_rate: 1.2 })).toEqual({
      mode: 'premium',
    })
    expect(audioFromPreferences({ ...preferences, voice_provider: 'openai', voice_name: 'marin' })).toEqual({ mode: 'premium' })
    expect(audioFromPreferences({ ...preferences, voice_provider: 'browser' })).toEqual({ mode: 'browser' })
  })
})

describe('spoken and remembered values', () => {
  it('writes seconds and whole minutes', () => {
    expect(spoken(30)).toBe('30 s')
    expect(spoken(120)).toBe('2 minutes')
    expect(spoken(60)).toBe('1 minute')
    expect(spoken(90)).toBe('90 s')
  })

  it('keeps the requested count in the session', () => {
    expect(recall(5)).toBeNull()
    remember(5, { count: 8, topicIds: [1, 2] })
    expect(recall(5)).toEqual({ count: 8, topicIds: [1, 2] })
    sessionStorage.clear()
  })
})

describe('quickVoice and quickAudio', () => {
  const row = (provider: string | null, name: string | null = null) => ({ ...LANGUAGES[1], voice_provider: provider, voice_name: name })

  it('uses the global engine for Polish with no voice of its own', () => {
    expect(quickVoice(preferences, 'pl', undefined)).toBe('piper')
    expect(quickVoice(preferences, 'pl', { ...LANGUAGES[0] })).toBe('piper')
  })

  it('prefers the voice set on the language row', () => {
    expect(quickVoice(preferences, 'pl', { ...LANGUAGES[0], voice_provider: 'openai', voice_name: 'cedar' })).toBe('openai')
    expect(quickVoice(preferences, 'en', row('browser'))).toBe('browser')
  })

  it("leaves another language's voice to the server when it has none", () => {
    expect(quickVoice(preferences, 'en', row(null))).toBeNull()
    expect(quickVoice(preferences, 'en', undefined)).toBeNull()
  })

  it('asks for the premium voice except for the browser voice', () => {
    expect(quickAudio('browser')).toEqual({ mode: 'browser' })
    expect(quickAudio(null)).toEqual({ mode: 'premium' })
    expect(quickAudio('piper')).toEqual({ mode: 'premium' })
  })
})
