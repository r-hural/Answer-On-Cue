import type { FeatureModule } from '../../app/types'
import { SettingsPage } from './SettingsPage'
import { UsagePage } from './UsagePage'
import { settingsStrings } from './strings'

const settings: FeatureModule = {
  routes: [
    { path: 'settings', element: <SettingsPage /> },
    { path: 'settings/usage', element: <UsagePage /> },
  ],
  nav: [{ path: '/settings', label: settingsStrings.nav, order: 80 }],
}

export const routes = settings.routes
export const nav = settings.nav
