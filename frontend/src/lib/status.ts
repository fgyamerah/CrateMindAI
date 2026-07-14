/**
 * Unified workflow/status model.
 *
 * Lifecycle: Detected → Classified → Proposed → Reviewed → Approved → Applied → Verified
 * These labels must be used consistently across the application. An approved
 * proposal is never presented as applied.
 */

export type ReviewStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'deferred'
  | 'partial'
  | 'applied'
  | 'verified'
  | 'failed'
  | 'skipped'
  | 'blocked'

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type PreflightStatus = 'ready' | 'degraded' | 'unsafe'

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'review' | 'neutral'

interface StatusMeta {
  label: string
  tone: Tone
}

export const REVIEW_STATUS_META: Record<ReviewStatus, StatusMeta> = {
  pending: { label: 'Pending review', tone: 'review' },
  approved: { label: 'Approved for apply', tone: 'info' },
  rejected: { label: 'Rejected', tone: 'neutral' },
  deferred: { label: 'Deferred', tone: 'neutral' },
  partial: { label: 'Partially reviewed', tone: 'review' },
  applied: { label: 'Applied', tone: 'success' },
  verified: { label: 'Verified', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  skipped: { label: 'Skipped', tone: 'neutral' },
  blocked: { label: 'Blocked', tone: 'warning' },
}

export const JOB_STATUS_META: Record<JobStatus, StatusMeta> = {
  pending: { label: 'Queued', tone: 'neutral' },
  running: { label: 'Running', tone: 'info' },
  succeeded: { label: 'Succeeded', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}

export const PREFLIGHT_STATUS_META: Record<PreflightStatus, StatusMeta> = {
  ready: { label: 'System ready', tone: 'success' },
  degraded: { label: 'Degraded', tone: 'warning' },
  unsafe: { label: 'Unsafe — action required', tone: 'danger' },
}

export function reviewStatusMeta(status: string | null | undefined): StatusMeta {
  if (status && status in REVIEW_STATUS_META) {
    return REVIEW_STATUS_META[status as ReviewStatus]
  }
  return { label: status || 'Unknown', tone: 'neutral' }
}

export function jobStatusMeta(status: string | null | undefined): StatusMeta {
  if (status && status in JOB_STATUS_META) {
    return JOB_STATUS_META[status as JobStatus]
  }
  return { label: status || 'Unknown', tone: 'neutral' }
}

export function preflightStatusMeta(status: string | null | undefined): StatusMeta {
  if (status && status in PREFLIGHT_STATUS_META) {
    return PREFLIGHT_STATUS_META[status as PreflightStatus]
  }
  return { label: 'Unknown', tone: 'neutral' }
}
