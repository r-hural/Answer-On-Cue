import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AttemptReport } from '../reports'
import {
  assessment,
  attemptReport,
  dontKnowReport,
  failedReport,
  metrics,
  noSpeechReport,
  reportResult,
  setupReportsWorld,
  weakReport,
  ai,
  askedBrief,
  goodReport,
  notAskedBrief,
  conflictReport,
  expandedReport,
  givenUpReport,
} from '../reports.mocks'
import { FOLLOW_UP_TEXT } from '../rounds.mocks'
import { FeedbackView, type FeedbackActions } from './FeedbackView'

function renderView(report: AttemptReport, actions?: FeedbackActions) {
  return render(
    <MemoryRouter>
      <FeedbackView report={report} actions={actions} />
    </MemoryRouter>,
  )
}

const headings = () => screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)

/** A one-letter Polish word followed by an ordinary space can end a line. */
const BREAKABLE = /(^|[\s(„"«])[aiouwzAIOUWZ] /

beforeEach(() => {
  setupReportsWorld()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FeedbackView: an assessed answer', () => {
  it('shows the four score cells from the factual result', () => {
    renderView(attemptReport())
    const scores = within(screen.getByLabelText('Scores'))
    expect(scores.getByText('Needs improvement')).toHaveClass('band', 'needs')
    expect(scores.getByText('Factual score 51 · correctness 70 · completeness 40')).toBeInTheDocument()
    expect(scores.getByText('54')).toBeInTheDocument()
    expect(scores.getByText('Language 66 (20% of this score)')).toBeInTheDocument()
    expect(scores.getByText('Speed')).toBeInTheDocument()
    expect(scores.getByText('8 s')).toBeInTheDocument()
    expect(scores.getByText('Preparation used · started speaking at once')).toBeInTheDocument()
    expect(scores.getByText('62')).toBeInTheDocument()
    expect(scores.getByText('approximate')).toBeInTheDocument()
    expect(scores.getByText('3 long pauses · fillers: at least 5')).toBeInTheDocument()
  })

  it('has the nine feedback sections in order', () => {
    renderView(attemptReport())
    expect(headings()).toEqual([
      'What was correct',
      'Factual errors',
      'What was missing',
      'Extra details',
      'Your answer',
      'Better version of your answer',
      'Language and wording',
      'Exam answer style',
      'Speed and fluency',
      'Sources',
    ])
  })

  it('sorts the points and marks a partial one with the partly mark and its note', () => {
    renderView(attemptReport())
    const correct = screen.getByRole('heading', { name: 'What was correct' }).nextElementSibling as HTMLElement
    const items = within(correct).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0].querySelector('svg.hit')).not.toBeNull()
    expect(items[1].querySelector('svg.part')).not.toBeNull()
    expect(within(items[1]).getByText('partly')).toHaveClass('chip', 'warn')
    expect(items[1]).toHaveTextContent('Podane niepewnie.')
    const missing = screen.getByRole('heading', { name: 'What was missing' }).nextElementSibling as HTMLElement
    expect(missing).toHaveTextContent('Kworum 6000 głosów.')
    expect(screen.getByRole('heading', { name: 'Factual errors' }).nextElementSibling).toHaveTextContent('None.')
  })

  it('lists factual mistakes with their correction and a major chip', () => {
    renderView(
      attemptReport({
        result: reportResult({
          assessment: assessment({
            ai: ai({
              factual_mistakes: [{ quote: 'na pięć lat', correction: 'na dziesięć lat', major: true, rule_index: 0 }],
              outside_material: [{ quote: 'bitwa pod Maratonem', judgement: 'valid', note: 'Trafne.' }],
              enrichment_mentioned: ['Arystydes'],
            }),
          }),
        }),
      }),
    )
    const errors = screen.getByRole('heading', { name: 'Factual errors' }).nextElementSibling as HTMLElement
    expect(errors).toHaveTextContent('„na pięć lat” → na dziesięć lat')
    expect(within(errors).getByText('major')).toHaveClass('chip', 'error')
    const extra = screen.getByRole('heading', { name: 'Extra details' }).nextElementSibling as HTMLElement
    expect(extra).toHaveTextContent('Arystydes')
    expect(extra).toHaveTextContent('bitwa pod Maratonem')
  })

  it('puts Polish content in lang="pl-PL" nodes with no breakable one-letter word', () => {
    renderView(attemptReport())
    // the text box of the correction holds the learner's own text, not typeset content
    const polish = [...document.querySelectorAll('[lang="pl-PL"]:not(textarea)')]
    expect(polish.length).toBeGreaterThan(10)
    for (const node of polish) expect(node.textContent ?? '').not.toMatch(BREAKABLE)
    for (const text of [
      'Wyjaśnij, na czym polegała procedura ostracyzmu',
      'Głosowanie na skorupach (ostrakonach)',
      'Ostracyzm był procedurą, w której obywatele',
      'Gramatyka poprawna.',
      'Zacznij od zdania definiującego ostracyzm.',
      'Raz w roku zgromadzenie decydowało',
    ]) {
      const node = screen.getByText(
        (_, element) => element?.textContent?.replace(/ /g, ' ').startsWith(text) === true && element.children.length === 0,
      )
      expect(node.closest('[lang="pl-PL"]')).not.toBeNull()
    }
    expect(document.querySelector('.transcript')).toHaveAttribute('lang', 'pl-PL')
    expect(document.querySelector('.transcript')).toHaveTextContent(/ostracyzm to było takie głosowanie/)
  })

  it('keeps the model answer in a closed disclosure for Needs improvement', () => {
    renderView(attemptReport({ model_answer_display: 'one_click' }))
    const disclosure = screen.getByText('Model answer', { selector: 'summary' }).closest('details') as HTMLDetailsElement
    expect(disclosure).toHaveClass('disclosure')
    expect(disclosure.open).toBe(false)
    expect(within(disclosure).getByText(/Ostracyzm był procedurą, w której zgromadzenie ludowe/)).toHaveAttribute('lang', 'pl-PL')
    expect(within(disclosure).getByText('Expanded explanation', { selector: 'summary' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Model answer' })).not.toBeInTheDocument()
  })

  it('shows the transcription doubts before the points', () => {
    renderView(
      attemptReport({
        result: reportResult({
          assessment: assessment({
            ai: ai({ transcription_doubts: [{ quote: 'ostrakonach', likely_meant: 'na ostrakonach', reason: 'Niewyraźne słowo.' }] }),
          }),
        }),
      }),
    )
    const notice = screen.getByText('The transcript may be wrong here').closest('.notice') as HTMLElement
    expect(notice).toHaveClass('warn')
    expect(notice).toHaveTextContent('„ostrakonach”')
    const points = screen.getByRole('heading', { name: 'What was correct' })
    expect(notice.compareDocumentPosition(points) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('reports a transcription mistake and confirms it', async () => {
    const world = setupReportsWorld()
    renderView(attemptReport())
    const user = userEvent.setup()
    await user.click(screen.getByText('Report a transcription mistake'))
    const box = screen.getByLabelText('What did you actually say?')
    expect(box).toHaveAttribute('lang', 'pl-PL')
    await user.clear(box)
    await user.type(box, 'na ostrakonach')
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByText('Report saved with this answer. Reassessment with the corrected text comes in a later version.')).toBeInTheDocument()
    expect(world.puts).toEqual([{ attemptId: 10, body: { corrected_text: 'na ostrakonach' } }])
  })

  it('says when the report could not be saved', async () => {
    const world = setupReportsWorld()
    world.putError = { status: 409, detail: 'There is no transcript to report on.' }
    renderView(attemptReport())
    const user = userEvent.setup()
    await user.click(screen.getByText('Report a transcription mistake'))
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByText('There is no transcript to report on.')).toBeInTheDocument()
  })
})

describe('FeedbackView: unavailable is not zero', () => {
  it('names a fluency that cannot be assessed', () => {
    renderView(attemptReport({ result: reportResult({ assessment: assessment({ ai: ai({ fluency: null }) }) }) }))
    const cell = screen.getByText('Fluency', { selector: '.k' }).parentElement as HTMLElement
    expect(cell).toHaveTextContent("Can't be assessed reliably")
    expect(cell).not.toHaveTextContent('approximate')
  })

  it('shows "unavailable" with the reason for every missing measurement', () => {
    renderView(
      attemptReport({
        result: reportResult({
          metrics: metrics({
            speech_start_latency_ms: null,
            words_per_minute: null,
            long_pause_count: null,
            filler_count: null,
            pauses: [],
            unavailable: {
              speech_start_latency_ms: 'no_speech',
              words_per_minute: 'no_transcript',
              long_pause_count: 'no_speech',
              filler_count: 'no_transcript',
            },
          }),
        }),
      }),
    )
    const facts = screen.getByRole('heading', { name: 'Speed and fluency' }).nextElementSibling as HTMLElement
    expect(within(facts).getByText('Start of speech').nextElementSibling).toHaveTextContent('unavailable (no speech)')
    expect(within(facts).getByText('Pace').nextElementSibling).toHaveTextContent('unavailable (no transcript)')
    expect(within(facts).getByText('Pauses').nextElementSibling).toHaveTextContent('unavailable (no speech)')
    expect(within(facts).getByText('Hesitations').nextElementSibling).toHaveTextContent('unavailable (no transcript)')
    expect(facts).not.toHaveTextContent(/\b0 words per minute|\b0 s\b/)
    expect(screen.getByLabelText('Scores')).toHaveTextContent('Preparation used · Start of speech: unavailable (no speech)')
  })
})

describe('FeedbackView: no answer, no speech, failure', () => {
  it('opens the model answer first for Weak, with the key points', () => {
    renderView(weakReport())
    const first = headings()[0]
    expect(first).toBe('Model answer')
    expect(screen.getByRole('heading', { name: 'Key points to remember' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').some((li) => li.textContent === 'Kworum 6000 głosów.')).toBe(true)
    expect(screen.queryByText('Model answer', { selector: 'summary' })).not.toBeInTheDocument()
    expect(screen.getByText('Weak')).toHaveClass('band', 'weak')
  })

  it('"I don\'t know": the band, no speed or fluency, and only the model answer', () => {
    renderView(dontKnowReport())
    expect(screen.getByText('You chose “I don\'t know” · factual score 0')).toBeInTheDocument()
    expect(screen.getByText('Language not assessed (no answer)')).toBeInTheDocument()
    expect(screen.getByText("Not counted for an answer you didn't give")).toBeInTheDocument()
    expect(screen.getByText('No speech to assess')).toBeInTheDocument()
    expect(headings()[0]).toBe('Model answer')
    expect(screen.queryByRole('heading', { name: 'Your answer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'What was correct' })).not.toBeInTheDocument()
  })

  it('no speech: a warning and the recording, no band, scores or model answer', () => {
    renderView(noSpeechReport())
    expect(screen.getByText('No speech was heard in this recording. It is not scored.').closest('.notice')).toHaveClass('warn')
    expect(screen.getByRole('group', { name: '1:52 recording' })).toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(screen.queryByText(/Model answer/)).not.toBeInTheDocument()
    expect(screen.queryByText('Transcript')).not.toBeInTheDocument()
  })

  it('a technical failure is unresolved, never a score', () => {
    renderView(failedReport('timeout'), { onRetry: vi.fn() })
    expect(screen.getByText('Unresolved')).toHaveClass('chip', 'error')
    expect(screen.getByText(/This answer could not be processed\./)).toBeInTheDocument()
    expect(screen.getByText(/The transcription service did not respond\./)).toBeInTheDocument()
    expect(screen.getByText(/This is a technical problem, not a score\./)).toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(screen.queryByText(/Model answer|Key points|Better version|Sources/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '1:52 recording' })).toBeInTheDocument()
  })

  it('audio_missing: resend when the WAV is still queued, no retry otherwise', async () => {
    const resend = vi.fn()
    const { unmount } = renderView(failedReport('audio_missing', false), { onResend: resend })
    expect(screen.getByText('Unresolved')).toBeInTheDocument()
    expect(screen.getByText(/never reached the app/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /recording/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Send the answer again' }))
    expect(resend).toHaveBeenCalledTimes(1)
    unmount()

    renderView(failedReport('audio_missing', false), {})
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(document.querySelector('.band')).toBeNull()
  })
})

describe('FeedbackView: playback', () => {
  function stubMedia() {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      Object.defineProperty(this, 'paused', { value: false, configurable: true })
      this.dispatchEvent(new Event('play'))
      return Promise.resolve()
    })
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
      Object.defineProperty(this, 'paused', { value: true, configurable: true })
      this.dispatchEvent(new Event('pause'))
    })
    return { play, pause }
  }

  it('plays and pauses the recording', async () => {
    const { play, pause } = stubMedia()
    renderView(attemptReport())
    const audio = document.querySelector('audio') as HTMLAudioElement
    expect(audio).toHaveAttribute('src', '/api/recordings/31/audio')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(play).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    expect(pause).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument()
  })

  it('ArrowRight on the position slider moves 5 s on', async () => {
    stubMedia()
    renderView(attemptReport())
    const slider = screen.getByRole('slider', { name: 'Position' })
    expect(slider).toHaveAttribute('aria-valuetext', '0:00 of 1:52')
    slider.focus()
    await userEvent.keyboard('{ArrowRight}')
    expect((document.querySelector('audio') as HTMLAudioElement).currentTime).toBe(5)
    await waitFor(() => expect(slider).toHaveAttribute('aria-valuetext', '0:05 of 1:52'))
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}')
    expect((document.querySelector('audio') as HTMLAudioElement).currentTime).toBe(0)
  })

  it('a pause mark seeks to one second before the pause and plays', async () => {
    const { play } = stubMedia()
    renderView(attemptReport())
    const timeline = within(screen.getByRole('group', { name: 'Pause timeline: 3 long pauses' }))
    expect(timeline.getAllByRole('button')).toHaveLength(3)
    await userEvent.click(timeline.getByRole('button', { name: 'Play the pause at 0:41, 4.1 seconds' }))
    expect((document.querySelector('audio') as HTMLAudioElement).currentTime).toBe(40)
    expect(play).toHaveBeenCalled()
  })

  it('a timeline without long pauses says so in its label and has no marks', () => {
    renderView(attemptReport({ result: reportResult({ metrics: metrics({ pauses: [], long_pause_count: 0 }) }) }))
    const timeline = screen.getByRole('group', { name: 'Pause timeline: no long pauses' })
    expect(within(timeline).queryAllByRole('button')).toHaveLength(0)
  })
})

describe('FeedbackView: the follow-up box', () => {
  const box = () => screen.getByRole('heading', { level: 2, name: 'Follow-up' }).closest('section') as HTMLElement

  it('an asked follow-up: its question, band and factual, note and link; kept apart', () => {
    renderView({ ...goodReport(), follow_up: askedBrief() })
    const section = within(box())
    expect(box()).toHaveClass('box', 'tint', 'flow')
    const question = section.getByText(new RegExp(FOLLOW_UP_TEXT.slice(0, 20)))
    expect(question).toHaveAttribute('lang', 'pl-PL')
    expect(question).toHaveClass('content')
    expect(section.getByText('Good')).toHaveClass('band', 'good')
    expect(section.getByText('factual 72')).toBeInTheDocument()
    expect(section.getByText(/Podano przykład Arystydesa/)).toHaveAttribute('lang', 'pl-PL')
    expect(section.getByRole('link', { name: 'Full follow-up feedback' })).toHaveAttribute('href', '/rounds/5/answers/21')
    expect(section.getByText("Follow-up scores are kept separately and do not change this question's score.")).toHaveClass('small', 'muted')
  })

  it('a follow-up answer that failed is unresolved, never a band', () => {
    renderView({ ...goodReport(), follow_up: askedBrief({ status: 'failed', band: null, factual: null }) })
    expect(within(box()).getByText('Unresolved')).toHaveClass('chip', 'error')
    expect(box().querySelector('.band')).toBeNull()
  })

  it('a follow-up answer still processing says so', () => {
    renderView({ ...goodReport(), follow_up: askedBrief({ status: 'processing', band: null, factual: null }) })
    expect(within(box()).getByText('Being processed')).toBeInTheDocument()
    expect(box().querySelector('.band')).toBeNull()
  })

  it.each([
    ['no_answer', 'No follow-up question: follow-ups are built from what you said, and there was no answer.'],
    ['main_unresolved', 'No follow-up question: this answer could not be processed, so there was nothing to build one from.'],
    ['generation_failed', 'No follow-up question: it could not be prepared. The provider is not responding.'],
    ['continued', 'No follow-up question: you continued while it was being prepared.'],
    ['not_reached', 'No follow-up question: the round ended before it was asked.'],
  ] as const)('not asked (%s): the reason, no band, no number', (reason, text) => {
    renderView({ ...goodReport(), follow_up: notAskedBrief(reason, 'The provider is not responding.') })
    expect(within(box()).getByText(text)).toBeInTheDocument()
    expect(box().querySelector('.band')).toBeNull()
    expect(box().textContent).not.toMatch(/\d/)
  })

  it('no box without a follow-up', () => {
    renderView({ ...goodReport(), follow_up: null })
    expect(screen.queryByRole('heading', { level: 2, name: 'Follow-up' })).toBeNull()
  })
})

describe('FeedbackView: source policy', () => {
  it('marks expanded and mixed points', () => {
    renderView(expandedReport({ points: expandedReport().points.map((p) => ({ ...p, status: 'covered' as const })) }))
    expect(screen.getByText('beyond your materials')).toHaveClass('note')
    expect(screen.getByText('partly beyond your materials')).toHaveClass('note')
    const covered = screen.getByText(/^Głosowanie na skorupach/).closest('li') as HTMLElement
    expect(within(covered).queryByText(/beyond your materials/)).toBeNull()
  })

  it('shows a source conflict with both passages, and leaves the point out of What was missing', () => {
    renderView(conflictReport())
    const title = screen.getByText('Your sources disagree on this point')
    const notice = title.closest('.notice') as HTMLElement
    expect(notice).toHaveClass('warn')
    expect(within(notice).getByText('Fragmenty podają różną liczbę głosów potrzebną do ważności.')).toHaveAttribute('lang', 'pl-PL')
    expect(notice.querySelectorAll('.source-quote')).toHaveLength(2)
    expect(within(notice).getByText('This point was not graded. Check these passages in your document.')).toBeInTheDocument()
    expect(within(notice).getByText('Kworum 6000 głosów.')).toBeInTheDocument()
    const missing = screen.getByRole('heading', { level: 2, name: 'What was missing' })
    const list = missing.nextElementSibling as HTMLElement
    expect(within(list).queryByText('Kworum 6000 głosów.')).toBeNull()
    expect(list).toHaveTextContent('None.')
  })

  it('a partly disputed answer keeps the disputed point out of the lists and says it was not graded', () => {
    renderView(conflictReport())
    const notice = screen.getByText('Your sources disagree on this point').closest('.notice') as HTMLElement
    expect(within(notice).getByText('This point was not graded. Check these passages in your document.')).toBeInTheDocument()
    expect(within(notice).queryByText(/graded as usual/)).toBeNull()
    const content = screen.getByRole('heading', { level: 2, name: 'What was correct' }).nextElementSibling as HTMLElement
    expect(content.querySelectorAll('li')).toHaveLength(2)
  })

  it('when the sources disagree on every point, all points are listed as graded', () => {
    const base = attemptReport()
    const conflict = conflictReport().source_conflicts![0]
    renderView(
      conflictReport({
        points: base.points.map((point) => ({ ...point, disputed: true })),
        source_conflicts: base.points.map((point) => ({ ...conflict, point_id: point.id, point_text: point.text })),
      }),
    )
    const notices = screen.getAllByText('Your sources disagree on this point').map((title) => title.closest('.notice') as HTMLElement)
    expect(notices).toHaveLength(3)
    for (const notice of notices) {
      expect(within(notice).queryByText(/not graded/)).toBeNull()
      expect(
        within(notice).getByText('Your sources disagree on every point, so all of them were graded as usual. Check these passages in your document.'),
      ).toBeInTheDocument()
    }
    const content = screen.getByRole('heading', { level: 2, name: 'What was correct' }).nextElementSibling as HTMLElement
    expect(content.querySelectorAll('li')).toHaveLength(2)
    const missing = screen.getByRole('heading', { level: 2, name: 'What was missing' }).nextElementSibling as HTMLElement
    expect(within(missing).getByText('Kworum 6000 głosów.')).toBeInTheDocument()
  })

  it('lists what goes beyond the materials inside the model answer disclosure', async () => {
    renderView(expandedReport())
    const summary = screen.getByText('Model answer', { selector: 'summary' })
    const details = summary.closest('details') as HTMLElement
    expect(within(details).getByText('Beyond your materials:')).toBeInTheDocument()
    const item = within(details).getByText('Ostatni znany ostracyzm: Hyperbolos około 417 roku p.n.e.')
    expect(item.closest('ul')).toHaveAttribute('lang', 'pl-PL')
  })

  it('shows no beyond list when it is empty or the model answer is withheld', () => {
    const { unmount } = renderView(expandedReport({ beyond_material: [] }))
    expect(screen.queryByText('Beyond your materials:')).toBeNull()
    unmount()
    renderView(expandedReport({ model_answer: null, model_answer_display: null }))
    expect(screen.queryByText('Beyond your materials:')).toBeNull()
  })

  it('renders a report without the source fields as before', () => {
    const { container } = renderView(attemptReport())
    const plain = container.innerHTML
    expect(screen.queryByText(/beyond your materials/)).toBeNull()
    expect(screen.queryByText('Your sources disagree on this point')).toBeNull()
    expect(screen.queryByText('Beyond your materials:')).toBeNull()
    expect(plain).toContain('Kworum 6000 głosów.')
  })
})

describe('FeedbackView: answer again and the retry round', () => {
  const original = {
    attempt_id: 9,
    outcome: 'answered',
    status: 'ready' as const,
    band: 'needs_improvement' as const,
    factual: 51,
    total: 54,
    preparation_used_ms: 8000,
    long_pause_count: 3,
    fluency: 62,
  }

  it('a repeat shows the first and the second answer, with help', () => {
    renderView({ ...goodReport(), attempt_id: 12, attempt_kind: 'immediate', original })
    const section = screen.getByRole('heading', { level: 2, name: 'First answer and second answer' }).closest('section') as HTMLElement
    const table = within(section).getByRole('table')
    expect(table).toHaveClass('table', 'wide', 'middle')
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      'Attempt',
      'Band',
      'Factual',
      'Knowledge and language',
      'Preparation used',
      'Long pauses',
      'Fluency',
    ])
    const [, first, second] = within(table).getAllByRole('row')
    expect(first).toHaveTextContent(/^First answer \(counts\)Needs improvement51548 s362$/)
    expect(within(first).getByText('Needs improvement')).toHaveClass('band', 'needs')
    expect(second).toHaveTextContent(/^Answer again With helpGood7475/)
    expect(within(second).getByText('With help')).toHaveClass('chip')
    expect(
      within(section).getByText(
        'The second answer came right after seeing the model answer, so it shows what you can say with help. Your round score keeps the first answer. Answer again is offered once per question.',
      ),
    ).toBeInTheDocument()
  })

  it('a retry-round answer compares with the original round', () => {
    renderView({ ...goodReport(), attempt_id: 12, attempt_kind: 'retry', round_type: 'retry', original })
    const table = within(screen.getByRole('heading', { level: 2, name: 'First answer and second answer' }).closest('section') as HTMLElement).getByRole('table')
    const [, first, second] = within(table).getAllByRole('row')
    expect(first).toHaveTextContent(/^Original round/)
    expect(second).toHaveTextContent(/^Retry round With help/)
  })

  it('an answered-again original links to its second answer', () => {
    renderView({ ...goodReport(), again_attempt_id: 12 })
    expect(screen.getByRole('link', { name: 'Your second answer (with help)' })).toHaveAttribute('href', '/rounds/5/answers/12')
    expect(screen.queryByRole('heading', { name: 'First answer and second answer' })).toBeNull()
  })
})

describe('FeedbackView: giving up an answer', () => {
  it('a given-up answer shows the not-assessed notice, the player and no panels', () => {
    renderView(givenUpReport())
    expect(screen.getByText('Not assessed.')).toBeInTheDocument()
    expect(
      screen.getByText('You gave up this answer, so it is left out of the round score. It is never counted as Weak.'),
    ).toBeInTheDocument()
    expect(document.querySelector('audio')).not.toBeNull()
    expect(screen.queryByRole('heading', { name: 'Factual errors' })).toBeNull()
    expect(document.querySelector('.scores')).toBeNull()
    expect(document.querySelector('.band')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Give up this answer' })).toBeNull()
  })

  it('a failed answer without onGiveUp has no give-up button', () => {
    renderView(failedReport(), { onRetry: () => undefined })
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Give up this answer' })).toBeNull()
  })

  it('asks before giving up', async () => {
    const onGiveUp = vi.fn()
    renderView(failedReport(), { onGiveUp })
    await userEvent.click(screen.getByRole('button', { name: 'Give up this answer' }))
    expect(screen.getByText("It is left out of the score as not assessed, and it can't be tried again.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Give up' })).toHaveClass('btn', 'small', 'danger')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onGiveUp).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Give up this answer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Give up' }))
    expect(onGiveUp).toHaveBeenCalledTimes(1)
  })
})
