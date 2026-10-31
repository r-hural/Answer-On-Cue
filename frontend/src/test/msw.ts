import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { costsHandlers } from '../features/costs/costs.mocks'
import { languageHandlers } from '../features/library/languages.mocks'

export const handlers = [
  http.get('/api/health', () =>
    HttpResponse.json({ status: 'ok', app: 'answeroncue', version: '0.1.0' }),
  ),
  // the shell's floating task card polls this on every page
  http.get('/api/jobs', () => HttpResponse.json([])),
  // estimates and the month box show on several screens: no ceiling, estimates that fit
  ...costsHandlers,
  // the subject forms list the enabled content languages: Polish only
  ...languageHandlers,
]

export const server = setupServer(...handlers)
