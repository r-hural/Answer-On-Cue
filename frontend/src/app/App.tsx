import { NavLink, Outlet } from 'react-router-dom'
import { LiveJobsCard } from '../features/library/LiveJobsCard'
import { CloseButton } from '../features/system/routes'
import { appStrings } from '../i18n/app'
import type { NavItem } from './types'

export function App({ nav }: { nav: NavItem[] }) {
  return (
    <>
      <header className="shell">
        <div className="shell-in">
          <NavLink className="brand" to="/">
            {appStrings.appName}
          </NavLink>
          <nav className="nav" aria-label="Main">
            {nav.map((item) => (
              <NavLink key={item.path} to={item.path} end={item.path === '/'}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="shell-end">
            <CloseButton />
          </div>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <LiveJobsCard />
    </>
  )
}
