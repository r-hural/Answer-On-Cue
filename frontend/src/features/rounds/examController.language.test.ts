import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeRead, fakeSession } from './examTestKit'
import { ExamController } from './examController'
import type { QuestionAudio } from './api'
import { QUESTIONS, config, setupRoundWorld } from './rounds.mocks'
import { createUploadQueue } from './uploads'

const PREMIUM: QuestionAudio = { mode: 'premium', provider: 'piper', voice: 'de_DE-thorsten-high', rate: 1 }

let controller: ExamController | null = null

afterEach(() => {
  controller?.dispose()
  controller = null
})

function start(read: ReturnType<typeof fakeRead>['read']): ExamController {
  return new ExamController(5, {
    open: async () => fakeSession().session,
    now: () => 1000,
    read,
    uploads: createUploadQueue(),
    pollMs: 50,
    noiseFloorDb: -60,
  })
}

describe("ExamController: the round's language reaches the voice", () => {
  it('reads each question with the language of the round', async () => {
    setupRoundWorld({ state: { config: config({ question_audio: PREMIUM, language: 'de' }) } })
    const voice = fakeRead()
    controller = start(voice.read)
    await controller.load()
    await vi.waitFor(() => expect(voice.read).toHaveBeenCalled())
    expect(voice.read).toHaveBeenCalledWith(QUESTIONS[0], { ...PREMIUM, language: 'de' })
  })

  it('sends no language for a round created before it was recorded', async () => {
    setupRoundWorld({ state: { config: config({ question_audio: PREMIUM }) } })
    const voice = fakeRead()
    controller = start(voice.read)
    await controller.load()
    await vi.waitFor(() => expect(voice.read).toHaveBeenCalled())
    expect(voice.read).toHaveBeenCalledWith(QUESTIONS[0], { ...PREMIUM, language: null })
  })
})
