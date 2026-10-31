import type { FeatureModule } from '../../app/types'
import { QuickPage } from './QuickPage'
import { QuickUploadPage } from './QuickUploadPage'

const quick: FeatureModule = {
  routes: [
    { path: 'quick', element: <QuickUploadPage /> },
    { path: 'quick/:subjectId', element: <QuickPage /> },
  ],
}

export const routes = quick.routes
