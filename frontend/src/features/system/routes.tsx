import type { FeatureModule } from '../../app/types'
import { CloseButton } from './CloseButton'
import { ProgramPage } from './ProgramPage'
import { systemStrings } from './strings'

const system: FeatureModule = {
  routes: [{ path: 'program', element: <ProgramPage /> }],
  nav: [{ path: '/program', label: systemStrings.nav, order: 95 }],
}

export const routes = system.routes
export const nav = system.nav
export { CloseButton }
