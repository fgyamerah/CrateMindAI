import type { JobStatus } from '../types/job'

const LABELS: Record<JobStatus, string> = {
  pending:   'Pending',
  running:   'Running',
  succeeded: 'Done',
  failed:    'Failed',
  cancelled: 'Cancelled',
}

interface Props {
  status: JobStatus
}

export default function StatusBadge({ status }: Props) {
  return (
    <span className={`badge badge--${status}`}>{LABELS[status]}</span>
  )
}
