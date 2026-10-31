import type { FeatureModule } from '../../app/types'
import { NewVersionPage } from './NewVersionPage'
import { SubjectListPage } from './SubjectListPage'
import { SubjectPage } from './SubjectPage'
import { TopicsEditPage } from './TopicsEditPage'
import { libraryStrings } from './strings'

const library: FeatureModule = {
  routes: [
    { path: 'library', element: <SubjectListPage /> },
    { path: 'library/:subjectId', element: <SubjectPage /> },
    { path: 'library/:subjectId/topics', element: <TopicsEditPage /> },
    { path: 'library/:subjectId/documents/:documentId/new-version', element: <NewVersionPage /> },
  ],
  nav: [{ path: '/library', label: libraryStrings.nav, order: 10 }],
}

export const routes = library.routes
export const nav = library.nav
