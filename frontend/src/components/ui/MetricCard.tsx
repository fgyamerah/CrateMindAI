import type { ReactNode } from 'react'

interface Props {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'default' | 'success' | 'warning' | 'danger'
  loading?: boolean
  unavailable?: string
  testId: string
}

export default function MetricCard({
  label,
  value,
  sub,
  tone = 'default',
  loading = false,
  unavailable,
  testId,
}: Props) {
  const toneClass = tone !== 'default' ? ` cm-metric--${tone}` : ''
  return (
    <div className={`cm-metric${toneClass}`} data-testid={testId}>
      <div className="cm-metric-label">{label}</div>
      {loading ? (
        <div className="cm-skeleton" style={{ width: 64, height: 24 }} aria-label={`${label} loading`} />
      ) : unavailable ? (
        <div className="cm-metric-value cm-metric-value--unavailable">{unavailable}</div>
      ) : (
        <div className="cm-metric-value">{value}</div>
      )}
      {sub && !loading ? <div className="cm-metric-sub">{sub}</div> : null}
    </div>
  )
}
