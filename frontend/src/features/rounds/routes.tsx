import type { FeatureModule } from '../../app/types'
import { AnswerPage } from './AnswerPage'
import { ExamPage } from './ExamPage'
import { RoundMicPage } from './RoundMicPage'
import { SetupPage } from './SetupPage'
import { SummaryPage } from './SummaryPage'

const rounds: FeatureModule = {
  routes: [
    { path: 'practice', element: <SetupPage /> },
    { path: 'practice/:roundId/microphone', element: <RoundMicPage /> },
    { path: 'rounds/:roundId/answers/:attemptId', element: <AnswerPage /> },
    { path: 'rounds/:roundId/summary', element: <SummaryPage /> },
  ],
  focusRoutes: [{ path: '/rounds/:roundId', element: <ExamPage /> }],
  nav: [{ path: '/practice', label: 'Practice', order: 20 }],
}

export const routes = rounds.routes
export const focusRoutes = rounds.focusRoutes
export const nav = rounds.nav
