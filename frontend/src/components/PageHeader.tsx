import type { ReactNode } from 'react'

interface Props {
  title:     string
  subtitle?: string
  actions?:  ReactNode
  badge?:    ReactNode
}

/**
 * Consistent top-of-page header used across all pages.
 * Renders title + optional subtitle on the left, optional badge inline
 * with title, and optional action buttons / indicators on the right.
 */
export default function PageHeader({ title, subtitle, actions, badge }: Props) {
  return (
    <div className="page-header">
      <div className="page-header-left">
        <div className="page-header-title-row">
          <h1 className="page-title">{title}</h1>
          {badge && <span className="page-header-badge">{badge}</span>}
        </div>
        {subtitle && <p className="page-subtitle-small">{subtitle}</p>}
      </div>
      {actions && (
        <div className="page-header-actions">{actions}</div>
      )}
    </div>
  )
}
