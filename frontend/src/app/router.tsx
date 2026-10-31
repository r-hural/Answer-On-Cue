import { createBrowserRouter, type RouteObject } from 'react-router-dom'
import { appStrings } from '../i18n/app'
import { App } from './App'
import type { FeatureModule, NavItem } from './types'

export function collectFeatures(modules: Record<string, FeatureModule>): {
  routes: RouteObject[]
  focusRoutes: RouteObject[]
  nav: NavItem[]
} {
  const values = Object.values(modules)
  const routes = values.flatMap((m) => m.routes)
  const focusRoutes = values.flatMap((m) => m.focusRoutes ?? [])
  const nav = values
    .flatMap((m) => m.nav ?? [])
    .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, 'en'))
  return { routes, focusRoutes, nav }
}

export const features = collectFeatures(
  import.meta.glob<FeatureModule>('../features/*/routes.tsx', { eager: true }),
)

export function createAppRouter() {
  return createBrowserRouter([
    ...features.focusRoutes,
    {
      path: '/',
      element: <App nav={features.nav} />,
      children: [
        ...features.routes,
        { path: '*', element: <p>{appStrings.notFound}</p> },
      ],
    },
  ])
}
