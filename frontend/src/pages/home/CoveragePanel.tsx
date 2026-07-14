import type { UseQueryResult } from '@tanstack/react-query'
import type { OverviewResponse } from './useHomeData'
import { formatPercent, percentValue } from '../../lib/format'
import { ErrorState } from '../../components/ui/StatePanel'

interface Row {
  label: string
  covered: number
  total: number
}

function CoverageRow({ label, covered, total }: Row) {
  const pct = percentValue(covered, total)
  const fillClass =
    pct >= 90 ? 'cm-coverage-fill' : pct >= 70 ? 'cm-coverage-fill cm-coverage-fill--warn' : 'cm-coverage-fill cm-coverage-fill--danger'
  return (
    <div className="cm-coverage-row" data-testid={`coverage-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <span className="cm-coverage-label">{label}</span>
      <div
        className="cm-coverage-bar"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} coverage`}
      >
        <div className={fillClass} style={{ width: `${pct}%` }} />
      </div>
      <span className="cm-coverage-value">{formatPercent(covered, total)}</span>
    </div>
  )
}

interface Props {
  query: UseQueryResult<OverviewResponse>
}

export default function CoveragePanel({ query }: Props) {
  const { data, isPending, isError, refetch } = query
  return (
    <div className="cm-panel" data-testid="coverage-panel">
      <div className="cm-panel-head">
        <h3>Library coverage</h3>
      </div>
      <div className="cm-panel-body">
        {isPending && (
          <div style={{ padding: '8px 16px', display: 'grid', gap: 8 }}>
            <div className="cm-skeleton" style={{ height: 14 }} />
            <div className="cm-skeleton" style={{ height: 14 }} />
            <div className="cm-skeleton" style={{ height: 14 }} />
          </div>
        )}
        {isError && (
          <div style={{ padding: 12 }}>
            <ErrorState detail="Coverage data could not be loaded." onRetry={() => refetch()} />
          </div>
        )}
        {data && (
          <>
            <CoverageRow
              label="Artist metadata"
              covered={data.total_tracks - data.tracks_missing_artist}
              total={data.total_tracks}
            />
            <CoverageRow
              label="Title metadata"
              covered={data.total_tracks - data.tracks_missing_title}
              total={data.total_tracks}
            />
            <CoverageRow label="BPM" covered={data.tracks_with_bpm} total={data.total_tracks} />
            <CoverageRow
              label="Camelot key"
              covered={data.tracks_with_camelot_key}
              total={data.total_tracks}
            />
          </>
        )}
      </div>
    </div>
  )
}
