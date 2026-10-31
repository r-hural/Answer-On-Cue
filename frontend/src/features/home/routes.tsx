import type { FeatureModule } from '../../app/types'
import { appStrings } from '../../i18n/app'
import { HomePage } from './HomePage'

const home: FeatureModule = {
  routes: [{ index: true, element: <HomePage /> }],
  nav: [{ path: '/', label: appStrings.nav.home, order: 0 }],
}

export const routes = home.routes
export const nav = home.nav
