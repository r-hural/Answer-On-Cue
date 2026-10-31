/** English UI strings of the home feature. */

export const homeStrings = {
  title: 'Home',
  loading: 'Loading…',
  welcome: 'Welcome to AnswerOnCue',
  lede: 'Practise answering exam questions out loud: the app asks a question, gives you time to prepare, records your answer and tells you what was right, what was missing and how fluently you spoke.',
  getStarted: 'Get started',
  addKey: 'Add an API key',
  addKeyHelp: 'Questions, transcription and feedback use an AI provider (OpenAI or Anthropic).',
  notDone: 'Not done yet',
  chooseVoice: 'Choose a voice',
  chooseVoiceHelp: 'Questions can be read aloud. The local Polish voice is free and works offline.',
  optional: 'Optional',
  checkMic: 'Check your microphone',
  checkMicHelp: 'Answers are spoken only, so the microphone must work.',
  checkMicButton: 'Check the microphone',
  addMaterials: 'Add materials',
  addMaterialsHelp:
    'Create a subject and upload PDF, DOCX or text files, or try quick practice with one file.',
  createSubject: 'Create a subject',
  quickPractice: 'Quick practice',
  yourData: 'Your data',
  yourDataBody:
    'Your materials, recordings and results stay on this computer. To ask and assess questions, the app sends your audio, the transcript and the relevant passages of your materials to the AI provider you choose. Local storage does not mean local AI processing.',
  privacyDetails: 'Privacy details',
  quickFromFile: 'Quick practice from a file',
  startRound: 'Start a round',
  subjects: 'Subjects',
  allSubjects: 'All subjects in the Library',
  noSubjects: 'No subjects yet.',
  documents: (count: number) => `${count} ${count === 1 ? 'document' : 'documents'}`,
  lastPractised: (day: string) => `Last practised ${day}`,
  practise: 'Practise',
  open: 'Open',
  recent: 'Recent rounds',
  history: 'History',
  noRounds: 'No rounds yet.',
  roundKind: (timing: string, roundType?: string) =>
    `${
      roundType === 'adaptive'
        ? 'Adaptive'
        : roundType === 'extension'
          ? 'Adaptive extension'
          : roundType === 'retry'
            ? 'Retry round'
            : 'Standard'
    } · ${
      timing === 'exam' ? 'Exam' : 'Practice'
    }`,
  questions: (n: number) => `${n} ${n === 1 ? 'question' : 'questions'}`,
  unfinishedChip: 'Unfinished',
  endedEarlyChip: 'Ended early',
  quickHelp:
    'Upload a PDF, DOCX or text file, confirm the topics and start answering. You can save it to a subject afterwards.',
  nextSteps: 'Next steps',
  unfinished: 'Unfinished round',
  unfinishedBody: (question: number, total: number, when: string) =>
    `, question ${question} of ${total}, started ${when}. Resume where you left off, or end it and see the results of the answers you gave.`,
  resume: 'Resume round',
  endRound: 'End round and see results',
}
