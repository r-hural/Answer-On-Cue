# AnswerOnCue

AnswerOnCue helps you practise for oral exams. You load your own study materials (PDF, DOCX,
TXT or Markdown), the app turns them into topics and exam questions, asks them one at a time
(read aloud if you like), records your spoken answer within a time limit, and gives feedback on
what you got right, what you missed, your wording and your fluency. Summaries and trends show
how you improve per subject and topic.

The interface is in English. The exam content (materials, questions, answers and feedback) can be
in Polish, English, German, French, Spanish, Italian or Ukrainian, chosen per subject. Polish is
enabled at first; turn on the others in Settings.

- Windows 10 or 11 only. No administrator rights needed.
- Runs on your computer and opens in your browser (`http://127.0.0.1`, local only).
- Uses AI services with your own API keys (see below). An internet connection is needed for them.

## Install

Open PowerShell and run:

```powershell
irm https://github.com/r-hural/Answer-On-Cue/releases/latest/download/install.ps1 | iex
```

The installer downloads [uv](https://github.com/astral-sh/uv) (a pinned version, checked
against its SHA-256), a private copy of Python 3.13 and the app package from this repository's
latest release (also checked against its SHA-256). It does not change your `PATH` or need
Python or Node on the computer. It creates an "AnswerOnCue" shortcut on the desktop and in the
Start menu and starts the app.

To stop the app, use "Close" in the header.

## API keys and what goes where

Add the keys on the Settings page (they are stored in `settings.env` in your data folder and
shown masked). You need at least one key for the AI features:

| Provider | Used for | What is sent |
|---|---|---|
| OpenAI | transcribing your spoken answers; generating questions and assessing answers (when OpenAI is chosen) | your answer recordings; excerpts of your materials, the questions, the transcripts |
| Anthropic | generating questions and assessing answers (when Anthropic is chosen) | excerpts of your materials, the questions, the transcripts |
| Azure Speech (optional) | reading questions aloud with an Azure voice | the question text |

Reading questions aloud can also use Piper, a voice that runs on your computer (the voice model
is downloaded once from Hugging Face), or your browser's own voice. The Program page checks
this repository's GitHub releases for updates.

Nothing else leaves your computer: no accounts, no telemetry.

## Your data

Everything is stored in `%LOCALAPPDATA%\AnswerOnCue\data`: the database, your uploaded
materials, recordings, voices, API keys (`settings.env`), database backups and logs. The data
survives updates. Settings has backup, export and restore, and can delete recordings.

- Program files: `%LOCALAPPDATA%\AnswerOnCue\app\<version>`.
- Update: the Program page, "Check again", then "Update". The database is copied before it
  changes; a failed update restores the copy and starts the previous version.

## Uninstall

Use the "Uninstall AnswerOnCue" shortcut in the Start menu, or run
`%LOCALAPPDATA%\AnswerOnCue\uninstall.ps1`. It asks whether to delete your data as well (type
`YES`).

## Run from source (developers)

Requirements: Windows, Python 3.13, Node LTS with npm.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1 -Build
```

`setup.ps1` creates `backend/.venv`, installs the packages, downloads Chromium for Playwright,
creates `data/` and runs the migrations. `dev.ps1 -Build` builds the interface and starts the
server on `http://127.0.0.1:8000`. Settings use the `AOC_` prefix; for development you can copy
`backend/env-example.txt` to `backend/.env` (never commit it).

Checks: `scripts\check.ps1` runs ruff, pytest, ESLint, Vitest, the build and the Playwright
tests and ends with `CHECK PASSED` (`-SkipE2E` skips Playwright). `scripts\package.ps1` builds
the release package into `release-out\v<version>\`.

## Contributing

Contributions are welcome. Before your first pull request can be merged, you need to agree to the
Contributor License Agreement in CONTRIBUTING.md. It lets the project stay GPL-3.0 for everyone
while the author may also offer it under other terms, including commercial ones.

## Licence

AnswerOnCue is free software under the GNU General Public License v3.0 (see `LICENSE`).
Copyright (C) 2026 r-hural. The bundled fonts (Public Sans, Source Serif 4) are under the SIL
Open Font License (`frontend/src/styles/fonts/`).

Security issues: see `SECURITY.md`.
