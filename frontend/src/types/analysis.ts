export type BpmReason =
  | 'missing_bpm'
  | 'too_low_10x'
  | 'likely_halved'
  | 'likely_doubled'
  | 'too_high'

export type AnomalyReviewStatus =
  | 'pending'
  | 'reviewed'
  | 'ignored'
  | 'requeued'
  | 'resolved'

export interface BpmAnomaly {
  id:                number
  track_id:          number
  filepath:          string
  artist:            string | null
  title:             string | null
  genre:             string | null
  current_bpm:       number | null
  suggested_bpm:     number | null
  reason:            BpmReason
  reason_label:      string
  review_status:     AnomalyReviewStatus
  detected_at:       string
  reviewed_at:       string | null
  review_note:       string | null
  reanalysis_job_id: string | null
}

export interface BpmCheckResult {
  tracks_scanned: number
  new_anomalies:  number
  resolved:       number
  total_active:   number
  items:          BpmAnomaly[]
}

export interface BpmSummary {
  by_status: Record<string, number>
  by_reason: Record<string, number>
}

export interface UpdateAnomalyRequest {
  review_status: AnomalyReviewStatus
  review_note?:  string
}

export interface ReanalyzeRequest {
  force:   boolean
  dry_run: boolean
}

export const REASON_COLORS: Record<BpmReason, string> = {
  missing_bpm:    'reason--missing',
  too_low_10x:    'reason--critical',
  likely_halved:  'reason--warn',
  likely_doubled: 'reason--warn',
  too_high:       'reason--critical',
}

export const STATUS_COLORS: Record<AnomalyReviewStatus, string> = {
  pending:  'anomaly-status--pending',
  reviewed: 'anomaly-status--reviewed',
  ignored:  'anomaly-status--ignored',
  requeued: 'anomaly-status--requeued',
  resolved: 'anomaly-status--resolved',
}
