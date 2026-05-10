import { apiFetch } from './client'

export type ReviewStatus = 'approved' | 'rejected' | 'deferred' | 'pending'

export interface EnrichmentQueueItem {
  filepath?: string | null
  track_id?: number | null
  provider?: string | null
  confidence?: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | null
  action_suggestion?: 'auto_candidate' | 'review' | 'ignore' | string | null
  score?: number | null
  review_status?: ReviewStatus | null
  review_updated_at?: string | null
  query?: Record<string, unknown> | null
  best_match?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface EnrichmentQueueResponse {
  items: EnrichmentQueueItem[]
  counts: Record<string, Record<string, number>>
  limit: number
  offset: number
  total: number
}

export interface ReviewStateItem {
  track_id: number
  review_status: Exclude<ReviewStatus, 'pending'>
  updated_at: string | null
}

export interface ReviewStateResponse {
  items: Record<string, ReviewStateItem>
  approved: number[]
  rejected: number[]
  deferred: number[]
  counts: Record<string, number>
  approved_high_count: number
  approved_medium_count: number
  rejected_by_reason: Record<string, number>
  queue_total: number
  updated_at: string | null
}

export interface ReviewSummaryResponse {
  pending_count: number
  approved_count: number
  rejected_count: number
  deferred_count: number
  approved_high_count: number
  approved_medium_count: number
  rejected_by_reason: Record<string, number>
  last_updated: string | null
}

export interface ApplyApprovedChange {
  track_id: number
  filepath: string
  fields: string[]
  before: Record<string, unknown>
  after: Record<string, unknown>
  confidence: string
  provider: string
  score: number | null
  review_status: string
}

export interface ApplyApprovedResponse {
  root: string
  db_path: string
  state_path: string
  log_path: string
  dry_run: boolean
  approved_seen: number
  proposed_count: number
  applied_count: number
  skipped_count: number
  changes: ApplyApprovedChange[]
  skipped: Array<Record<string, unknown>>
}

export function fetchLatestAudit(): Promise<Record<string, unknown>> {
  return apiFetch.get<Record<string, unknown>>('/audit/latest')
}

export function fetchEnrichmentQueue(params?: {
  action?: 'auto_candidate' | 'review' | 'ignore' | null
  confidence?: 'HIGH' | 'MEDIUM' | 'LOW' | null
  limit?: number
  offset?: number
}): Promise<EnrichmentQueueResponse> {
  const query = new URLSearchParams()
  if (params?.action) query.set('action', params.action)
  if (params?.confidence) query.set('confidence', params.confidence)
  if (params?.limit != null) query.set('limit', String(params.limit))
  if (params?.offset != null) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch.get<EnrichmentQueueResponse>(`/enrichment/queue${suffix}`)
}

export function fetchReviewState(): Promise<ReviewStateResponse> {
  return apiFetch.get<ReviewStateResponse>('/enrichment/review/state')
}

export function fetchReviewSummary(): Promise<ReviewSummaryResponse> {
  return apiFetch.get<ReviewSummaryResponse>('/enrichment/review/summary')
}

export function approveReview(trackId: number) {
  return apiFetch.post<{ track_id: number; review_status: 'approved'; state: ReviewStateResponse }>(
    `/enrichment/review/${trackId}/approve`,
    {},
  )
}

export function rejectReview(trackId: number) {
  return apiFetch.post<{ track_id: number; review_status: 'rejected'; state: ReviewStateResponse }>(
    `/enrichment/review/${trackId}/reject`,
    {},
  )
}

export function deferReview(trackId: number) {
  return apiFetch.post<{ track_id: number; review_status: 'deferred'; state: ReviewStateResponse }>(
    `/enrichment/review/${trackId}/defer`,
    {},
  )
}

export function dryRunApplyApproved(): Promise<ApplyApprovedResponse> {
  return apiFetch.post<ApplyApprovedResponse>('/enrichment/apply-approved/dry-run', {})
}

export function applyApproved(confirm = false): Promise<ApplyApprovedResponse> {
  const suffix = confirm ? '?confirm=true' : ''
  return apiFetch.post<ApplyApprovedResponse>(`/enrichment/apply-approved/apply${suffix}`, {})
}
