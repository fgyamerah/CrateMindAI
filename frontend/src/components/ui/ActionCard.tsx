import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface Props {
  icon: LucideIcon
  tone: 'warning' | 'danger' | 'review' | 'info' | 'success'
  title: string
  detail: string
  to: string
  testId: string
}

export default function ActionCard({ icon: Icon, tone, title, detail, to, testId }: Props) {
  return (
    <Link to={to} className="cm-action-card" data-testid={testId}>
      <span className={`cm-action-icon cm-action-icon--${tone}`} aria-hidden="true">
        <Icon size={17} strokeWidth={1.9} />
      </span>
      <span className="cm-action-body">
        <span className="cm-action-title">{title}</span>
        <span className="cm-action-detail" style={{ display: 'block' }}>{detail}</span>
      </span>
      <ChevronRight size={16} className="cm-action-chevron" aria-hidden="true" />
    </Link>
  )
}
