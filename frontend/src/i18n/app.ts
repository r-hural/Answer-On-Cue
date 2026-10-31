export const appStrings = {
  appName: 'AnswerOnCue',
  nav: { home: 'Home' },
  health: { ok: 'Connected to the server', error: 'Cannot reach the server' },
  privacy: {
    title: 'Privacy.',
    body: 'The program runs on your computer, but the AI does not run locally. Recordings of your answers are sent to a transcription service (OpenAI). Transcripts, questions and the needed parts of your documents are sent to the provider you chose for generating and grading (OpenAI or Anthropic). With an Azure voice, the question text is sent to Azure Speech; the Piper voice runs on this computer. API keys are stored only on this computer, in the program data folder, and are sent only to the provider they belong to.',
  },
  notFound: 'Page not found',
} as const
