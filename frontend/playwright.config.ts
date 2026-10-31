import { defineConfig, devices, type Project } from '@playwright/test'
import path from 'node:path'

const port = Number(process.env.AOC_TEST_PORT ?? 8765)
const python = path.resolve(import.meta.dirname, '../backend/.venv/Scripts/python.exe')
const fixture = (name: string) => path.resolve(import.meta.dirname, 'e2e/fixtures', name)

/** Chromium with a fake microphone that plays (and loops) a WAV fixture. */
function micProject(name: string, wav: string, testMatch: RegExp): Project {
  return {
    name,
    testMatch,
    use: {
      ...devices['Desktop Chrome'],
      permissions: ['microphone'],
      launchOptions: {
        args: [
          '--use-fake-ui-for-media-stream',
          '--use-fake-device-for-media-stream',
          `--use-file-for-fake-audio-capture=${fixture(wav)}`,
        ],
      },
    },
  }
}

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results/artifacts',
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    headless: true,
  },
  projects: [
    { name: 'chromium', testMatch: /smoke\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'ops', testMatch: /ops\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'library-ui', testMatch: /library-ui\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'coverage', testMatch: /coverage\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'source-policy', testMatch: /source-policy\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'library-fixes', testMatch: /library-fixes\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'chapters-ui', testMatch: /chapters-ui\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'languages', testMatch: /languages\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'doc-versions', testMatch: /doc-versions\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'chapters-integration', testMatch: /chapters-integration\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'trends', testMatch: /trends\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'backup', testMatch: /backup\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'costs', testMatch: /costs\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'openai-voice', testMatch: /openai-voice\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'give-up', testMatch: /give-up\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'cleanup', testMatch: /cleanup\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    micProject('mic-speech', 'speech_mid.wav', /mic\.speech\.spec\.ts/),
    micProject('mic-click', 'click_only.wav', /mic\.click\.spec\.ts/),
    micProject('exam', 'speech_mid.wav', /exam\.spec\.ts/),
    micProject('feedback', 'speech_mid.wav', /feedback\.spec\.ts/),
    micProject('follow-ups', 'speech_mid.wav', /follow-ups\.spec\.ts/),
    micProject('adaptive', 'click_only.wav', /adaptive\.spec\.ts/),
    micProject('repetition', 'click_only.wav', /repetition\.spec\.ts/),
    micProject('recovery', 'click_only.wav', /recovery\.spec\.ts/),
    micProject('content-language', 'speech_mid.wav', /content-language\.spec\.ts/),
    {
      name: 'mic-denied',
      testMatch: /mic\.denied\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        permissions: [],
        // Without a fake UI, headless Chromium cannot prompt and rejects with NotSupportedError;
        // the fake UI in deny mode gives the real NotAllowedError a user's "Block" produces.
        launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream=deny'] },
      },
    },
  ],
  webServer: {
    // The recording tests write to the database, so it is migrated first; the exam tests need
    // the "E2E exam" subject, which the seed adds once.
    command: `"${python}" -m alembic upgrade head && "${python}" ../frontend/e2e/seed_exam.py && "${python}" -m uvicorn e2e_app:app --app-dir ../frontend/e2e --host 127.0.0.1 --port ${port}`,
    cwd: '../backend',
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    env: {
      AOC_DATA_DIR: path.resolve(import.meta.dirname, 'test-results/data'),
    },
  },
})
