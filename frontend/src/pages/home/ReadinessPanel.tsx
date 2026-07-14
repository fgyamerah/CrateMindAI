import { CheckCircle2, AlertTriangle, XCircle, RefreshCw } from 'lucide-react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { PreflightResponse } from '../../api/runtime'
import { preflightStatusMeta } from '../../lib/status'
import StatusPill from '../../components/ui/StatusPill'
import { ErrorState } from '../../components/ui/StatePanel'

const ICONS = {
  pass: CheckCircle2,
  warn: AlertTriangle,
  fail: XCircle,
} as const

interface Props {
  query: UseQueryResult<PreflightResponse>
}

export default function ReadinessPanel({ query }: Props) {
  const { data, isPending, isError, refetch } = query

  return (
    <div className="cm-panel" data-testid="readiness-panel">
      <div className="cm-panel-head">
        <h3>System readiness</h3>
        {data && (
          <StatusPill
            tone={preflightStatusMeta(data.status).tone}
            label={preflightStatusMeta(data.status).label}
            testId="readiness-status-pill"
          />
        )}
        <button
          type="button"
          className="cm-icon-btn"
          onClick={() => refetch()}
          aria-label="Re-run readiness checks"
          data-testid="readiness-refresh-button"
        >
          <RefreshCw size={14} />
        </button>
      </div>
      <div className="cm-panel-body">
        {isPending && (
          <div style={{ padding: '8px 16px', display: 'grid', gap: 8 }}>
            <div className="cm-skeleton" style={{ height: 16 }} />
            <div className="cm-skeleton" style={{ height: 16 }} />
            <div className="cm-skeleton" style={{ height: 16 }} />
          </div>
        )}
        {isError && (
          <div style={{ padding: 12 }}>
            <ErrorState
              detail="Readiness checks could not be loaded from the backend."
              onRetry={() => refetch()}
              testId="readiness-error"
            />
          </div>
        )}
        {data &&
          data.checks.map((check) => {
            const Icon = ICONS[check.status] ?? AlertTriangle
            return (
              <div className="cm-check-row" key={check.id} data-testid={`readiness-check-${check.id}`}>
                <Icon
                  size={15}
                  className={`cm-check-icon cm-check-icon--${check.status}`}
                  aria-label={check.status}
                />
                <div style={{ minWidth: 0 }}>
                  <div className="cm-check-label">{check.label}</div>
                  <div className="cm-check-detail">{check.detail}</div>
                  {check.status !== 'pass' && check.remediation && (
                    <div className="cm-check-remedy">{check.remediation}</div>
                  )}
                </div>
              </div>
            )
          })}
      </div>
    </div>
  )
}
