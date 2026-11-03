# Changelog

## 0.1.3

- The app now opens at `http://answer-on-cue.localhost:8770` instead of `http://127.0.0.1:8770`.
  It still runs only on your computer. The browser treats the new address as a new site, so it
  asks for the microphone once more, and old bookmarks should be updated.
- Questions read aloud by a Piper, Azure or OpenAI voice no longer wait for their voice to be
  made: the next question's voice is prepared while you answer, and the first one while the
  microphone is checked. A question still waiting for its voice shows "Preparing the question…"
  and appears together with the reading (after 10 seconds at most).
- Spinners show the waits: preparing the voice, the steps of assessing an answer, and pages
  that are loading.
- Piper voices no longer crash the app on computers with an older Microsoft Visual C++
  Redistributable. Piper needs version 14.40 or later; with an older one, reading a question or
  previewing a Piper voice shows a message with the download link, and another voice can be
  chosen in Settings. The installer warns about it too.

## 0.1.1

- The repository's secret scan no longer reports the made-up keys used in the tests.

## 0.1.0 (first public release)

- Study materials: upload PDF, DOCX, TXT or Markdown files into subjects, group books into
  chapters, replace a document with a new version, and see the topics found in each.
- Questions: generated from your materials (only your materials, or expanded with general
  knowledge), with expected answers and scoring rubrics; review, edit and approve them.
- Exam rounds: standard and adaptive rounds with separate preparation and answer
  timers, optional examiner follow-ups, immediate repetition, end-of-round retries, pause and
  resume. Questions can be read aloud (Piper, Azure Speech or the browser's voice).
- Spoken answers: recorded in the browser, transcribed, and assessed for content, language and
  fluency, with a model answer when an answer is weak.
- Content languages: Polish, English, German, French, Spanish, Italian and Ukrainian, chosen
  per subject; questions, assessment and feedback follow the subject's language.
- Progress: round summaries, history, coverage by topic and trends over time.
- Costs: an estimate before paid work, usage per provider, and spending limits.
- Settings: API keys (stored locally, shown masked), voice choice, microphone test, backup,
  export, restore and deleting recordings.
- Windows installer with one PowerShell command; in-app updates; uninstaller.
