/** English UI strings of the recording feature (kept here, not in i18n/app.ts). */

/** Whole decibels with a typographic minus, e.g. "−42". */
export function formatDb(db: number): string {
  const rounded = Math.round(db)
  return rounded < 0 ? `−${-rounded}` : `${rounded}`
}

/** Seconds with one decimal, e.g. "2.0". */
export function formatSeconds(ms: number): string {
  return (ms / 1000).toFixed(1)
}

export const recordingStrings = {
  nav: 'Microphone',
  errors: {
    denied: 'Microphone access is blocked. Allow the microphone in your browser and reload the page.',
    notFound: 'No microphone found.',
    failed: 'Could not start the microphone.',
    upload: 'Could not upload the recording.',
    analyze: 'Could not analyse the recording.',
  },
  micCheck: {
    title: 'Microphone check',
    intro:
      'Check your microphone before studying: allow access, measure the background noise in a quiet room, then say a few sentences.',
    allow: 'Allow microphone',
    opening: 'Connecting to the microphone…',
    ready: 'The microphone works.',
    resampled: 'Your browser does not support 16 kHz; the audio is being resampled.',
    meterLabel: 'Sound level',
    calibrate: 'Calibrate silence',
    calibrating: 'Stay quiet for 3 seconds…',
    noiseFloor: (db: number) => `Noise level: ${formatDb(db)} dB`,
    listenTest: 'Listening test',
    listenHint: 'Start speaking at any time — recording starts by itself.',
    preparation: 'Preparation',
    countdown: (seconds: number) => `Preparation: ${seconds} s`,
    speechDetected: 'Speech detected — recording',
    timerStarted: 'Preparation over — recording',
    recording: 'Recording',
    stop: 'Stop',
    uploading: 'Uploading and analysing the recording…',
    playback: 'Play back the recording',
    speechRange: (fromMs: number, toMs: number) =>
      `Speech from ${formatSeconds(fromMs)} s to ${formatSeconds(toMs)} s`,
    noSpeech: 'No speech detected in the recording.',
    again: 'Repeat the test',
    step: (n: number) => `Step ${n}: `,
    stepAccess: 'Access',
    stepQuiet: 'Quiet',
    stepSample: 'Sample',
    quietLead: "Stay silent for 3 seconds so the app learns the room's background noise.",
    sampleLead: 'Start the test, then say a few sentences.',
    afterAccess: 'Runs after the app can hear the microphone.',
    listening: 'Listening',
  },
}
