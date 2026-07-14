import { Link } from 'react-router-dom'
import type { UseQueryResult } from '@tanstack/react-query'
import type { Job } from '../../types/job'
import { jobStatusMeta } from '../../lib/status'
import StatusPill from '../../components/ui/StatusPill'
import { formatRelativeTime } from '../../lib/format'
import { ErrorState } from '../../components/ui/StatePanel'

interface Props {
  query: UseQueryResult<Job[]>
}

export default function RecentJobsPanel({ query }: Props) {
  const { data, isPending, isError, refetch } = query

  return (
    <div className="cm-panel" data-testid="recent-jobs-panel">
      <div className="cm-panel-head">
        <h3>Recent jobs</h3>
        <Link to="/jobs" className="cm-btn cm-btn--ghost" style={{ padding: '4px 10px', fontSize: 12 }} data-testid="recent-jobs-view-all">
          View all
        </Link>
      </div>
      <div className="cm-panel-body">
        {isPending && (
          <div style={{ padding: '8px 16px', display: 'grid', gap: 8 }}>
            <div className="cm-skeleton" style={{ height: 16 }} />
            <div className="cm-skeleton" style={{ height: 16 }} />
          </div>
        )}
        {isError && (
          <div style={{ padding: 12 }}>
            <ErrorState
              detail="Job history could not be loaded."
              onRetry={() => refetch()}
              testId="recent-jobs-error"
            />
          </div>
        )}
        {data && data.length === 0 && (
          <div className="cm-panel-empty" data-testid="recent-jobs-empty">
            No jobs have been run yet. Pipeline operations appear here.
          </div>
        )}
        {data &&
          data.slice(0, 6).map((job) => {
            const meta = jobStatusMeta(job.status)
            return (
              <Link to="/jobs" className="cm-job-row" key={job.id} data-testid={`recent-job-${job.id}`}>
                <span className="cm-job-cmd">{job.command}</span>
                <StatusPill tone={meta.tone} label={meta.label} />
                <span className="cm-job-time">{formatRelativeTime(job.created_at)}</span>
              </Link>
            )
          })}
      </div>
    </div>
  )
}
