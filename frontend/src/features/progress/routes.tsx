import { Navigate } from 'react-router-dom'
import type { FeatureModule } from '../../app/types'
import { CoveragePage } from './CoveragePage'
import { HistoryPage } from './HistoryPage'
import { progressStrings } from './strings'
import { TopicCoveragePage } from './TopicCoveragePage'
import { TrendsPage } from './TrendsPage'

const progress: FeatureModule = {
  routes: [
    { path: 'progress', element: <Navigate to="/progress/history" replace /> },
    { path: 'progress/coverage', element: <CoveragePage /> },
    { path: 'progress/topics/:topicId', element: <TopicCoveragePage /> },
    { path: 'progress/history', element: <HistoryPage /> },
    { path: 'progress/trends', element: <TrendsPage /> },
  ],
  nav: [{ path: '/progress', label: progressStrings.nav, order: 30 }],
}

export const routes = progress.routes
export const nav = progress.nav
