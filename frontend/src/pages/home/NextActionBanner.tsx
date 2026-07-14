import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { NextAction } from '../../lib/nextAction'

interface Props {
  action: NextAction | null
  loading: boolean
}

export default function NextActionBanner({ action, loading }: Props) {
  if (loading) {
    return (
      <div className="cm-next-action" data-testid="next-action-loading">
        <div style={{ flex: 1 }}>
          <div className="cm-skeleton" style={{ width: 120, height: 12, marginBottom: 8 }} />
          <div className="cm-skeleton" style={{ width: 320, height: 18 }} />
        </div>
      </div>
    )
  }
  if (!action) return null
  return (
    <div className="cm-next-action" data-testid="next-action-banner">
      <div>
        <div className="cm-next-action-eyebrow">Recommended next action</div>
        <div className="cm-next-action-title" data-testid="next-action-title">
          {action.title}
        </div>
        <div className="cm-next-action-detail">{action.detail}</div>
      </div>
      <Link to={action.to} className="cm-btn" data-testid="next-action-cta">
        {action.cta}
        <ArrowRight size={15} aria-hidden="true" />
      </Link>
    </div>
  )
}
