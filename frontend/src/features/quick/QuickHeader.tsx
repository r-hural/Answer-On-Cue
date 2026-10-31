import { Link } from 'react-router-dom'
import { quickStrings } from './strings'

/** Breadcrumb, heading and the four stages shared by every quick-practice step. */
export function QuickHeader({ stage }: { stage: number }) {
  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">{quickStrings.home}</Link> <span aria-hidden="true">›</span> {quickStrings.title}
      </nav>
      <div className="page-head">
        <div>
          <h1>{quickStrings.title}</h1>
          <p>{quickStrings.lede}</p>
          <ol className="stages mt-5" aria-label={quickStrings.steps}>
            {quickStrings.stages.map((label, n) => (
              <li
                key={label}
                className={n < stage ? 'done' : undefined}
                aria-current={n === stage ? 'step' : undefined}
              >
                {label}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </>
  )
}
