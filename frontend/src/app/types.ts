import type { RouteObject } from 'react-router-dom'

export interface NavItem {
  path: string
  label: string
  order: number
}

export interface FeatureModule {
  routes: RouteObject[]
  /** Full-page routes mounted beside the App shell (the exam screen brings its own header). */
  focusRoutes?: RouteObject[]
  nav?: NavItem[]
}
