/** English UI strings of the costs feature (estimates, month box, usage labels). */

export const costsStrings = {
  operations: {
    'attempt.transcribe': 'Transcription',
    'attempt.assess': 'Answer assessment',
    'follow_up.generate': 'Follow-up questions',
    'topics.extract': 'Topic extraction',
    'questions.generate': 'Question generation',
    'voice.synthesize': 'Question voice',
    'voice.verify': 'Question voice check',
  } as Record<string, string>,
  providers: {
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    azure_speech: 'Azure Speech',
    piper: 'Piper',
    browser: 'Browser voice',
  } as Record<string, string>,
  generationLine: (questions: number, minutes: string, usd: string) =>
    `Estimate: ${questions} ${questions === 1 ? 'question' : 'questions'}, ${minutes} and ${usd} of AI usage.`,
  extractionLine: (usd: string) => `Analysing topics: about ${usd} of AI usage.`,
  onlyLeft: (left: string, ceiling: string) =>
    ` Only ${left} is left of your ${ceiling} monthly ceiling.`,
  monthTitle: 'This month',
  monthRounds: (rounds: number) => `${rounds} ${rounds === 1 ? 'round' : 'rounds'}`,
  monthUsage: (usd: string) => `AI usage about ${usd}`,
  monthOfCeiling: (ceiling: string) => `of the ${ceiling} ceiling`,
  meter: (percent: number) => `${percent}% of the monthly ceiling used`,
  usageLink: 'Usage',
}

export const operationLabel = (key: string) => costsStrings.operations[key] ?? key
export const providerLabel = (key: string) => costsStrings.providers[key] ?? key
