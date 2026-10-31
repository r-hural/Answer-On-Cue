import type { FeatureModule } from '../../app/types'
import { BankReviewPage } from './BankReviewPage'

// Reached from the subject page of the library, so there is no nav entry.
const bank: FeatureModule = {
  routes: [{ path: 'library/:subjectId/questions', element: <BankReviewPage /> }],
}

export const routes = bank.routes
