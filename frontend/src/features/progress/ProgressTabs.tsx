import { Link } from 'react-router-dom'
import { progressStrings as t } from './strings'

/** The views of the progress screens; Coverage and Trends keep the chosen subject. */
export function ProgressTabs({ current, subjectId }: { current: 'coverage' | 'history' | 'trends'; subjectId?: number | null }) {
  const coverage = subjectId ? `/progress/coverage?subject=${subjectId}` : '/progress/coverage'
  const trends = subjectId ? `/progress/trends?subject=${subjectId}` : '/progress/trends'
  return (
    <nav className="tabs" aria-label={t.tabsLabel}>
      <Link to={coverage} aria-current={current === 'coverage' ? 'page' : undefined}>
        {t.tabCoverage}
      </Link>
      <Link to="/progress/history" aria-current={current === 'history' ? 'page' : undefined}>
        {t.tabHistory}
      </Link>
      <Link to={trends} aria-current={current === 'trends' ? 'page' : undefined}>
        {t.trends.tab}
      </Link>
    </nav>
  )
}
