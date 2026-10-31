import type { FeatureModule } from '../../app/types'
import { MicCheckPage } from './MicCheckPage'
import { recordingStrings } from './strings'

const recording: FeatureModule = {
  routes: [{ path: 'microphone', element: <MicCheckPage /> }],
  nav: [{ path: '/microphone', label: recordingStrings.nav, order: 90 }],
}

export const routes = recording.routes
export const nav = recording.nav
