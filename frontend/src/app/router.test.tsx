import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { collectFeatures } from './router'
import type { FeatureModule } from './types'

describe('collectFeatures', () => {
  it('collects routes and sorts nav', () => {
    const modules: Record<string, FeatureModule> = {
      b: { routes: [{ path: '/b' }], nav: [{ path: '/b', label: 'B', order: 2 }] },
      a: { routes: [{ path: '/a' }], nav: [{ path: '/a', label: 'A', order: 1 }] },
    }
    const { routes, nav } = collectFeatures(modules)
    expect(routes).toHaveLength(2)
    expect(nav.map((n) => n.order)).toEqual([1, 2])
  })

  it('allows a module without nav', () => {
    const { routes, nav } = collectFeatures({ x: { routes: [{ path: '/x' }] } })
    expect(routes).toHaveLength(1)
    expect(nav).toEqual([])
  })
})

describe('focus routes', () => {
  const modules: Record<string, FeatureModule> = {
    a: {
      routes: [{ path: 'a', element: <p>normal page</p> }],
      focusRoutes: [{ path: '/focus', element: <p>focus page</p> }],
    },
  }

  it('collects focus routes apart from the shell routes', () => {
    const { routes, focusRoutes } = collectFeatures(modules)
    expect(routes).toHaveLength(1)
    expect(focusRoutes).toHaveLength(1)
  })

  it('renders a focus route without the navigation, normal routes with it', () => {
    const { routes, focusRoutes } = collectFeatures(modules)
    const make = (path: string) =>
      createMemoryRouter(
        [...focusRoutes, { path: '/', element: <App nav={[]} />, children: routes }],
        { initialEntries: [path] },
      )
    const focus = render(<RouterProvider router={make('/focus')} />)
    expect(screen.getByText('focus page')).toBeInTheDocument()
    expect(document.querySelector('header.shell')).toBeNull()
    focus.unmount()
    render(<RouterProvider router={make('/a')} />)
    expect(screen.getByText('normal page')).toBeInTheDocument()
    expect(document.querySelector('header.shell')).not.toBeNull()
  })
})
