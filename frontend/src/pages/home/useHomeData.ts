/**
 * Server-state hooks for the command-center home.
 *
 * Every consumer must distinguish loading, error, and real data.
 * A failed query is surfaced as "unavailable" — never rendered as zero.
 */
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../../api/client'
import { fetchHealth } from '../../api/health'
import { getRuntimePreflight } from '../../api/runtime'
import { fetchJobs } from '../../api/jobs'

export interface StatsResponse {
  tracks_count: number
  disk_audio_files: number
  missing_files: number
  untracked_files: number
  stale_processed_state_total: number
  canonical_source: string
  last_audit_report: { generated_at?: string } | null
}

export interface OverviewResponse {
  total_tracks: number
  tracks_with_bpm: number
  tracks_with_camelot_key: number
  tracks_missing_artist: number
  tracks_missing_title: number
}

interface RepairSummary {
  queue_total: number
  pending_count: number
  approved_count: number
  applied_count: number
}

interface EnrichmentQueueCounts {
  total: number
  counts: { by_action: Record<string, number>; by_confidence: Record<string, number> }
}

interface EnrichmentReviewSummary {
  pending_count: number
  approved_count: number
  rejected_count: number
  deferred_count: number
}

interface BpmSummary {
  by_status: Record<string, number>
  by_reason: Record<string, number>
}

export function useHomeData() {
  const preflight = useQuery({ queryKey: ['runtime-preflight'], queryFn: getRuntimePreflight })
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const stats = useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch.get<StatsResponse>('/stats'),
  })
  const overview = useQuery({
    queryKey: ['library-overview'],
    queryFn: () => apiFetch.get<OverviewResponse>('/library/overview'),
  })
  const repair = useQuery({
    queryKey: ['metadata-repair-summary'],
    queryFn: () => apiFetch.get<RepairSummary>('/metadata-repair/summary'),
  })
  const sanitation = useQuery({
    queryKey: ['metadata-sanitation-summary'],
    queryFn: () => apiFetch.get<RepairSummary>('/metadata-sanitation/summary'),
  })
  const enrichmentQueue = useQuery({
    queryKey: ['enrichment-queue-counts'],
    queryFn: () => apiFetch.get<EnrichmentQueueCounts>('/enrichment/queue?limit=1'),
  })
  const enrichmentReview = useQuery({
    queryKey: ['enrichment-review-summary'],
    queryFn: () => apiFetch.get<EnrichmentReviewSummary>('/enrichment/review/summary'),
  })
  const bpm = useQuery({
    queryKey: ['bpm-anomalies-summary'],
    queryFn: () => apiFetch.get<BpmSummary>('/analysis/bpm-anomalies/summary'),
  })
  const jobs = useQuery({
    queryKey: ['recent-jobs'],
    queryFn: () => fetchJobs(8),
    refetchInterval: 20_000,
  })

  return {
    preflight,
    health,
    stats,
    overview,
    repair,
    sanitation,
    enrichmentQueue,
    enrichmentReview,
    bpm,
    jobs,
  }
}

export function enrichmentReviewable(
  queue: EnrichmentQueueCounts | undefined,
  review: EnrichmentReviewSummary | undefined,
): number {
  if (!queue) return 0
  const byAction = queue.counts?.by_action ?? {}
  const candidates = (byAction['review'] ?? 0) + (byAction['auto_candidate'] ?? 0)
  const decided = review
    ? review.approved_count + review.rejected_count + review.deferred_count
    : 0
  return Math.max(0, candidates - decided)
}

export function bpmPendingCount(summary: BpmSummary | undefined): number {
  if (!summary) return 0
  return summary.by_status?.['pending'] ?? 0
}
