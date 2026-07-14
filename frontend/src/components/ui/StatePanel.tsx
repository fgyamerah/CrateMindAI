import { AlertTriangle, Inbox, Loader2 } from 'lucide-react'

interface ErrorProps {
  title?: string
  detail: string
  onRetry?: () => void
  testId?: string
}

export function ErrorState({ title = 'Failed to load', detail, onRetry, testId }: ErrorProps) {
  return (
    <div className="cm-state cm-state--error" role="alert" data-testid={testId}>
      <div className="cm-state-title">
        <AlertTriangle size={15} aria-hidden="true" /> {title}
      </div>
      <div className="cm-state-detail">{detail}</div>
      {onRetry && (
        <button type="button" className="cm-btn cm-btn--ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

interface EmptyProps {
  title: string
  detail?: string
  testId?: string
}

export function EmptyState({ title, detail, testId }: EmptyProps) {
  return (
    <div className="cm-state" data-testid={testId}>
      <div className="cm-state-title">
        <Inbox size={15} aria-hidden="true" /> {title}
      </div>
      {detail && <div className="cm-state-detail">{detail}</div>}
    </div>
  )
}

export function LoadingState({ label = 'Loading…', testId }: { label?: string; testId?: string }) {
  return (
    <div className="cm-state" role="status" aria-live="polite" data-testid={testId}>
      <div className="cm-state-title">
        <Loader2 size={15} className="cm-spin" aria-hidden="true" /> {label}
      </div>
    </div>
  )
}
